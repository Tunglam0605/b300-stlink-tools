"""Production Monitor observes the application's shared project and connection."""
from pathlib import Path
from typing import Callable, Optional
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget, QScrollArea
from b300_core.models import ProbeRef
from b300_core.remote_profile import load_remote_profile
from b300_core.typed_symbols import TypedSymbolCatalog
from b300_core.variable_watch import (
    WatchCompileError, collect_watchable_node_ids, compile_watches,
)
from b300_gui.production_live_panel import ProductionLivePanel
from b300_gui.live_monitor_controller import LiveMonitorController, LiveMonitorRequest
from b300_gui.variable_tree_panel import VariableTreePanel
from b300_gui.workers import FunctionWorker

class MonitorView(QWidget):
    operation_state_changed = Signal(bool)
    log = Signal(str)
    manage_gateways_requested = Signal()
    manage_projects_requested = Signal()
    open_vscode_requested = Signal()

    def __init__(self, parent=None, *, context=None, live_panel=None, controller=None,
                 selected_probe: Optional[Callable[[], ProbeRef]] = None,
                 openocd_executable=None, remote_session_provider=None, hardware_busy=None,
                 remote_profile_loader=load_remote_profile,
                 catalog_worker_factory=FunctionWorker):
        super().__init__(parent)
        self.setObjectName("monitorViewContainer")
        self._context = None
        self._symbols = None
        self._fallback_project = None
        self._fallback_gateway = None
        self._typed_source = None
        self._catalog_worker_factory = catalog_worker_factory
        self._catalog_workers = set()
        self._catalog_generation = 0
        self.variable_tree_panel = VariableTreePanel(self)
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
        self.variable_tree_panel.load_requested.connect(self._load_typed_symbols_requested)
        self.variable_tree_panel.add_watch_requested.connect(self._add_typed_watch)
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
        body.setSpacing(8)
        self.live_panel.setMinimumHeight(620)
        self.variable_tree_panel.setMinimumWidth(340)
        self.variable_tree_panel.setMinimumHeight(620)
        body.addWidget(self.variable_tree_panel, 3)
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
        selected_symbols = self._selected_symbols()
        if selected_symbols is not None:
            selected_symbols = Path(selected_symbols).expanduser().resolve()
        if selected_symbols != self._typed_source:
            self._typed_source = selected_symbols
            self.variable_tree_panel.set_source(selected_symbols)
            self.variable_tree_panel.set_catalog(None)
            if selected_symbols is not None:
                self.begin_typed_symbol_load(selected_symbols)

    def _render_sample_summary(self, sample):
        self.last_sample.setText("Mẫu gần nhất: %.3f s" % sample.captured_elapsed_seconds)
        quality = "giá trị không nhất quán" if any(not value.coherent for value in sample.values) else "giá trị nhất quán"
        self.sample_health.setText("Chất lượng mẫu: " + quality)
        for value in sample.values:
            self.variable_tree_panel.model.update_live_value(
                value, sample.captured_elapsed_seconds,
            )

    def _clear_sample_summary(self):
        self.last_sample.setText("Mẫu gần nhất: —")
        self.sample_health.setText("Chất lượng mẫu: chưa kiểm tra")

    def mark_gateway_stale(self, reason):
        """Invalidate remote samples without erasing their last observed value."""
        selected = str(reason or "Gateway không còn cung cấp bằng chứng mới.").strip()
        self.controller.invalidate(selected)
        self.variable_tree_panel.model.mark_values_stale(selected)
        self.sample_health.setText("Chất lượng mẫu: STALE · %s" % selected)

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
        self.load_typed_symbols(selected)

    def load_typed_symbols(self, path):
        selected = Path(path).expanduser().resolve()
        catalog = TypedSymbolCatalog(selected)
        self._typed_source = selected
        self.variable_tree_panel.set_source(selected)
        self.variable_tree_panel.set_catalog(catalog)
        return catalog

    def begin_typed_symbol_load(self, path):
        """Parse a potentially large AXF/ELF without blocking Qt's GUI thread."""
        selected = Path(path).expanduser().resolve()
        self._typed_source = selected
        self.variable_tree_panel.set_source(selected)
        self.variable_tree_panel.set_catalog(None)
        self.variable_tree_panel.status.setText("Đang đọc kiểu DWARF từ %s…" % selected.name)
        self._catalog_generation += 1
        generation = self._catalog_generation

        def operation(_log, _phase, _cancel):
            return TypedSymbolCatalog(selected)

        worker = self._catalog_worker_factory(operation, self)
        self._catalog_workers.add(worker)

        def completed(catalog):
            if generation == self._catalog_generation and selected == self._typed_source:
                self.variable_tree_panel.set_catalog(catalog)

        def failed(failure):
            if generation == self._catalog_generation and selected == self._typed_source:
                self.variable_tree_panel.status.setText(
                    getattr(failure, "message", str(failure))
                )

        def finished():
            self._catalog_workers.discard(worker)
            worker.deleteLater()

        worker.completed.connect(completed)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        worker.start()
        return worker

    def _load_typed_symbols_requested(self):
        symbols = self._selected_symbols()
        if symbols is None:
            self.variable_tree_panel.status.setText("Chọn dự án có tệp AXF/ELF trước khi nạp cây biến.")
            return
        self.begin_typed_symbol_load(symbols)

    def prepare_symbol_shutdown(self, timeout_ms=3000):
        """Bound cleanup so no catalog worker outlives the Qt view."""
        workers = tuple(self._catalog_workers)
        for worker in workers:
            if worker.isRunning():
                worker.cancel()
        for worker in workers:
            if worker.isRunning() and not worker.wait(int(timeout_ms)):
                return False
        return True

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self.prepare_symbol_shutdown()
        super().closeEvent(event)

    def _add_typed_watch(self, node_id):
        catalog = self.variable_tree_panel.catalog
        if catalog is None:
            self.variable_tree_panel.status.setText("Nạp catalog DWARF trước khi thêm Watch Live.")
            return
        try:
            current = tuple(getattr(watch, "node_id", None)
                            for watch in self.live_panel.compiled_watches())
            current_ids = tuple(item for item in current if item)
            selected_ids = collect_watchable_node_ids(catalog, str(node_id))
            added_ids = tuple(item for item in selected_ids if item not in current_ids)
            node_ids = current_ids + added_ids
            watches = compile_watches(catalog, node_ids)
            by_id = {watch.node_id: watch for watch in watches}
            for selected_id in added_ids:
                self.live_panel.add_compiled_watch(by_id[selected_id])
            self.variable_tree_panel.status.setText(
                "%d biến scalar đã được thêm Watch Live." % len(added_ids)
                if added_ids else "Các biến scalar đã có trong Watch Live."
            )
        except (WatchCompileError, RuntimeError, ValueError) as error:
            self.variable_tree_panel.status.setText(str(error))

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
