"""Production Monitor observes the application's shared project and connection."""
from pathlib import Path
import hashlib
from typing import Callable, Optional
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget, QScrollArea
from b300_core.models import ProbeRef
from b300_core.remote_profile import load_remote_profile
from b300_core.typed_symbols import TypedSymbolCatalog
from b300_core.variable_watch import (
    WatchCompileError, collect_watchable_node_ids, compile_watches, rebind_watches,
)
from b300_core.watch_profiles import load_watch_policies, save_watch_policies
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
        self._digest_check_pending = False
        self._catalog_generation = 0
        self._typed_revision = None
        self._pending_rebind_watches = ()
        self._watch_policy_path = None
        self._watch_policy_load_error = None
        self._hardware_is_busy = False
        self._symbol_poll_timer = QTimer(self)
        self._symbol_poll_timer.setInterval(1000)
        self._symbol_poll_timer.timeout.connect(self._poll_typed_source)
        self._symbol_poll_timer.start()
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
        set_context = getattr(self.controller, "set_context", None)
        if callable(set_context):
            set_context(context)
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
            self.live_panel.watch_policies_changed.connect(self._save_project_watch_policies)
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
        self._load_project_watch_policies(project)
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
                self.live_panel.start_button.setEnabled(False)
                self.live_panel.status.setText("Đang xác minh AXF/ELF trước khi theo dõi.")
                self.begin_typed_symbol_load(selected_symbols)

    @staticmethod
    def _watch_policy_sidecar(project):
        workspace = getattr(project, "workspace", None)
        return Path(workspace).expanduser().resolve() / ".b300-watch-policies.json" if workspace else None

    def _load_project_watch_policies(self, project):
        sidecar = self._watch_policy_sidecar(project)
        if sidecar == self._watch_policy_path:
            return
        self._watch_policy_path = sidecar
        self._watch_policy_load_error = None
        if not isinstance(self.live_panel, ProductionLivePanel):
            return
        # A project boundary must never retain policies loaded for another
        # project's symbols, including when the new sidecar is malformed.
        self.live_panel.set_watch_policies(())
        if sidecar is None or not sidecar.is_file():
            return
        try:
            self.live_panel.set_watch_policies(load_watch_policies(sidecar))
        except ValueError as error:
            self._watch_policy_load_error = str(error)
            self.live_panel.status.setText("Không thể đọc chính sách Watch: %s" % error)

    def _save_project_watch_policies(self, policies):
        if self._watch_policy_path is None:
            self.live_panel.status.setText("Chọn dự án có workspace trước khi lưu chính sách Watch.")
            return
        if self._watch_policy_load_error is not None:
            self.live_panel.status.setText(
                "Không thể lưu chính sách Watch vì sidecar hiện tại bị lỗi; "
                "hãy sửa tệp hoặc chuyển lại dự án để nạp lại."
            )
            return
        try:
            save_watch_policies(self._watch_policy_path, policies)
        except (OSError, ValueError) as error:
            self.live_panel.mark_failed("Không thể lưu chính sách Watch: %s" % error)

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

    def observe_gateway_health(self, _snapshot=None):
        """Route GUI health ticks through the Monitor-owned client binding."""
        state = self.controller.accept_gateway_health_snapshot(_snapshot)
        if state is not None and getattr(state, "state", None) != "READY":
            self.sample_health.setText("Chất lượng mẫu: STALE · %s" % getattr(state, "reason_code", "Gateway"))
        return state

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
        self._typed_revision = self._revision(selected, catalog.fingerprint)
        self._publish_axf(catalog.fingerprint)
        return catalog

    def _publish_axf(self, fingerprint):
        if self._context is not None and self._typed_source is not None:
            self._context.apply_device_state(
                axf_basename=self._typed_source.name, axf_fingerprint=str(fingerprint),
                reason="AXF/ELF changed",
            )

    @staticmethod
    def _revision(path, fingerprint=None):
        selected = Path(path).expanduser().resolve()
        info = selected.stat()
        return (int(info.st_size), int(info.st_mtime_ns), fingerprint)

    def _poll_typed_source(self):
        selected = self._typed_source
        if selected is None or self._catalog_workers:
            return
        try:
            current = self._revision(selected)
        except OSError as error:
            if self.controller.active:
                self.controller.invalidate("Tệp AXF/ELF không còn khả dụng: %s" % error)
            self.variable_tree_panel.status.setText("Tệp AXF/ELF không còn khả dụng.")
            return
        previous = self._typed_revision
        if previous is None or current[:2] != previous[:2]:
            self._typed_revision = None
            invalidator = getattr(self.live_panel, "invalidate_compiled_watches", None)
            if callable(invalidator) and not self._pending_rebind_watches:
                self._pending_rebind_watches = tuple(invalidator(
                    "AXF/ELF đã thay đổi; địa chỉ cũ đã bị thu hồi."
                ))
            self.live_panel.start_button.setEnabled(False)
            if self.controller.active:
                self.controller.invalidate(
                    "AXF/ELF đã thay đổi; dừng mẫu cũ để tránh dùng địa chỉ DWARF lỗi thời."
                )
            self.begin_typed_symbol_load(selected, hot_reload=True)
        elif not self._digest_check_pending:
            self._begin_digest_check(selected, current)

    def _begin_digest_check(self, selected, revision):
        """Hash unchanged metadata in a worker so restored mtimes fail closed."""
        self._digest_check_pending = True
        self.live_panel.start_button.setEnabled(False)
        generation = self._catalog_generation
        def operation(_log, _phase, _cancel):
            digest = hashlib.sha256(Path(selected).read_bytes()).hexdigest()
            return digest, self._revision(selected)
        worker = self._catalog_worker_factory(operation, self)
        self._catalog_workers.add(worker)
        def completed(result):
            digest, current = result
            if (generation == self._catalog_generation and selected == self._typed_source
                    and self._typed_revision is not None and current[:2] == revision[:2]
                    and digest != self._typed_revision[2]):
                self._typed_revision = None
                self.live_panel.start_button.setEnabled(False)
                self.variable_tree_panel.status.setText("AXF/ELF đã đổi nội dung; đang nạp lại catalog an toàn.")
                self.begin_typed_symbol_load(selected, hot_reload=True)
        def finished():
            self._digest_check_pending = False
            self._catalog_workers.discard(worker)
            worker.deleteLater()
            catalog = self.variable_tree_panel.catalog
            if (self._typed_revision is not None and catalog is not None
                    and self._typed_revision[2] == getattr(catalog, "fingerprint", None)
                    and not self.controller.active and not self._hardware_is_busy):
                self.live_panel.start_button.setEnabled(True)
        worker.completed.connect(completed); worker.failed.connect(lambda _failure: None); worker.finished.connect(finished)
        worker.start()

    def begin_typed_symbol_load(self, path, *, hot_reload=False):
        """Parse a potentially large AXF/ELF without blocking Qt's GUI thread."""
        selected = Path(path).expanduser().resolve()
        self._typed_source = selected
        self.variable_tree_panel.set_source(selected)
        self._typed_revision = None
        self.live_panel.start_button.setEnabled(False)
        if not hot_reload:
            self.variable_tree_panel.set_catalog(None)
        self.variable_tree_panel.status.setText("Đang đọc kiểu DWARF từ %s…" % selected.name)
        self._catalog_generation += 1
        generation = self._catalog_generation
        previous_watches = (
            tuple(self._pending_rebind_watches)
            if hot_reload else tuple(self.live_panel.compiled_watches())
        )

        def operation(_log, _phase, _cancel):
            before = self._revision(selected)
            catalog = TypedSymbolCatalog(selected)
            after = self._revision(selected)
            return catalog, before, after

        worker = self._catalog_worker_factory(operation, self)
        self._catalog_workers.add(worker)

        def completed(result):
            if generation == self._catalog_generation and selected == self._typed_source:
                catalog, before, after = result
                if before[:2] != after[:2]:
                    self._typed_revision = None
                    self.live_panel.start_button.setEnabled(False)
                    self.variable_tree_panel.status.setText(
                        "AXF/ELF tiếp tục thay đổi trong lúc đọc; đang chờ bản ổn định."
                    )
                    return
                self.variable_tree_panel.set_catalog(catalog)
                self._typed_revision = (after[0], after[1], catalog.fingerprint)
                self._publish_axf(catalog.fingerprint)
                if hot_reload and previous_watches:
                    rebound, stale = rebind_watches(catalog, previous_watches)
                    apply_rebound = getattr(self.live_panel, "apply_rebound_watches", None)
                    if callable(apply_rebound):
                        apply_rebound(rebound, stale)
                    self.variable_tree_panel.status.setText(
                        "AXF/ELF đã cập nhật · %d watch giữ lại · %d watch STALE."
                        % (len(rebound), len(stale))
                    )
                    self._pending_rebind_watches = ()
                self.live_panel.start_button.setEnabled(
                    not self.controller.active and not self._hardware_is_busy
                )

        def failed(failure):
            if generation == self._catalog_generation and selected == self._typed_source:
                self._typed_revision = None
                self.live_panel.start_button.setEnabled(False)
                self.variable_tree_panel.status.setText(
                    "Không thể nạp AXF/ELF; sửa tệp hoặc chọn bản build ổn định rồi thử lại. %s" %
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
        self._symbol_poll_timer.stop()
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
            self.variable_tree_panel.add_button.setEnabled(False)

    def set_project_profiles(self, profiles, default_id=None):
        items = tuple(profiles)
        self._fallback_project = next((p for p in items if p.project_id == default_id), items[0] if items else None)
        self._render_context()

    def set_gateway_profiles(self, profiles, default_id=None):
        items = tuple(profiles)
        self._fallback_gateway = next((p for p in items if p.profile_id == default_id), items[0] if items else None)
        self._render_context()

    def set_hardware_busy(self, busy):
        self._hardware_is_busy = bool(busy)
        self.live_panel.start_button.setEnabled(
            not self._hardware_is_busy and self._typed_revision is not None
            and not self.controller.active
        )

    def _start_requested(self):
        symbols = self._selected_symbols()
        connection = getattr(self._context, "selected_connection", None)
        if symbols is None:
            self.live_panel.status.setText("Chọn dự án có tệp ELF/AXF trên thanh dùng chung.")
            return
        catalog = self.variable_tree_panel.catalog
        if (self._digest_check_pending or self._typed_revision is None or catalog is None
                or self._typed_revision[2] != getattr(catalog, "fingerprint", None)):
            self.live_panel.mark_failed(
                "AXF/ELF chưa ổn định hoặc catalog DWARF đã cũ; chờ nạp lại hoàn tất."
            )
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
