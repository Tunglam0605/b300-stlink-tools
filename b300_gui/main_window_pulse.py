"""Simple native Pulse Dock backed by the canonical B300 production services."""
from __future__ import annotations

from collections import deque
from pathlib import Path
from PySide6.QtCore import QTimer, QThread
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QFileDialog, QMenu, QPushButton, QStackedWidget, QVBoxLayout, QWidget
from b300_core.vscode_bridge import BridgeState, DebugRole
from .branding import asset_path
from .main_window import MainWindow
from .production_window import ProductionMainWindow
from .operation_state import OperationState
from .pulse_login_dialog import PulseLoginDialog
from .pulse_tasks import PulseTasks
from .pulse_view import PulseView
from .theme import ThemeManager


class MainWindowPulse(ProductionMainWindow):
    def __init__(self, *args, **kwargs):
        self._pulse_close_requested = False
        self._pulse_cleanup_done = False
        self._pulse_jobs = deque()
        self._pulse_handlers = {}
        self._pulse_selection = None
        self._pulse_selection_revision = 0
        self._pulse_debug_connection_id = None
        super().__init__(*args, **kwargs)
        self._pulse_tasks = PulseTasks(self)
        self._pulse_tasks.busy_changed.connect(self._pulse_busy_changed)
        self._pulse_tasks.completed.connect(self._pulse_completed)
        self._pulse_tasks.failed.connect(self._pulse_failed)
        self._vscode_controller.set_lifecycle_scheduler(self._schedule_lifecycle)
        # The busy gate prevents a start while monitoring owns SWD. No Qt
        # monitor callback may be invoked from the background start worker.
        self._vscode_controller.set_monitor_handoff(None)
        advanced = self.takeCentralWidget()
        self._pulse_stack = QStackedWidget()
        self.pulse_view = PulseView()
        self._pulse_stack.addWidget(self.pulse_view)
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        back = QPushButton('← Giao diện đơn giản')
        back.clicked.connect(lambda: self._pulse_stack.setCurrentIndex(0))
        layout.addWidget(back)
        layout.addWidget(advanced, 1)
        self._pulse_stack.addWidget(wrapper)
        self.setCentralWidget(self._pulse_stack)
        self.menuBar().hide()
        self.statusBar().hide()
        self.setWindowTitle('B300 · ST-Link Tools')
        self.setWindowIcon(QIcon(str(asset_path('pulse-logo.png'))))
        self.resize(1120, 720)
        view = self.pulse_view
        view.setup_requested.connect(lambda: self.show_machine_setup())
        view.login_requested.connect(self._pulse_login)
        view.options_requested.connect(self._pulse_options)
        view.project_manager_requested.connect(self._open_project_manager)
        view.project_selected.connect(self.app_context.select_project)
        view.connection_selected.connect(self.app_context.select_connection)
        view.mode_changed.connect(self._pulse_mode_changed)
        view.file_requested.connect(self._pulse_choose_file)
        view.action_requested.connect(self._pulse_action)
        view.stop_requested.connect(self._on_v18_stop_bridge)
        self.app_context.changed.connect(self._pulse_render)
        ThemeManager.instance().theme_changed.connect(self._pulse_theme)
        self._pulse_theme()
        self._pulse_render()

    def _pulse_theme(self, *_):
        if hasattr(self, 'pulse_view'):
            self.pulse_view.set_dark(ThemeManager.instance().palette.is_dark)

    def _refresh_reference_palette(self, *args):
        palette = ThemeManager.instance().palette
        if getattr(self, '_pulse_applied_palette', None) == palette:
            self._apply_density()
            return
        self._pulse_applied_palette = palette
        super()._refresh_reference_palette(*args)

    def _operation_state(self):
        state = super()._operation_state()
        tasks = getattr(self, '_pulse_tasks', None)
        controller = getattr(self, '_vscode_controller', None)
        owned_debug = bool(controller and (controller.state.role is not None or controller._lease_token))
        return OperationState(state.main_hardware_busy or bool(tasks and tasks.busy),
                              state.memory_hardware_busy, state.debug_hardware_busy or owned_debug)

    def _update_controls(self):
        super()._update_controls()
        self._pulse_render()

    def _pulse_render(self, *_):
        if not hasattr(self, 'pulse_view'):
            return
        gateway = self.app_context.selected_connection.gateway
        if self.pulse_view.mode == 'debug':
            self._pulse_debug_connection_id = self.app_context.selected_connection.connection_id
        selection = (self.app_context.selected_project, self.app_context.selected_connection)
        if selection != self._pulse_selection:
            self._pulse_selection = selection
            self._pulse_selection_revision += 1
        state = self._vscode_controller.state
        self.pulse_view.render(
            self.app_context,
            busy=self._pulse_tasks.busy or bool(self._threads) or self.busy or self.monitor_view.controller.active,
            debug_active=state.role is not None or bool(self._vscode_controller._lease_token),
            ssh_connected=bool(gateway and self._gateway_sessions.connected(gateway.endpoint)),
        )
        self.pulse_view.set_file(self.program_view._selected_file)

    def _pulse_mode_changed(self, mode):
        if self._operation_state().is_hardware_busy:
            return
        if mode == 'program':
            self._pulse_debug_connection_id = self.app_context.selected_connection.connection_id
            self.app_context.select_connection('local')
            self.pulse_view.set_status('Sẵn sàng nạp chương trình.' if self.program_view._selected_file else 'Chọn tệp chương trình để bắt đầu.')
        elif mode == 'debug':
            selected = self._pulse_debug_connection_id
            if any(item.connection_id == selected for item in self.app_context.connections):
                self.app_context.select_connection(selected)
            gateway = self.app_context.selected_connection.gateway
            connected = gateway and self._gateway_sessions.connected(gateway.endpoint)
            self.pulse_view.set_status('Sẵn sàng mở debug.' if gateway is None or connected else 'Đăng nhập SSH để debug từ xa.')

    def _pulse_options(self):
        menu = QMenu(self)
        menu.addAction('Dự án…', self._open_project_manager)
        menu.addAction('Máy SSH…', self._open_gateway_manager)
        menu.addSeparator()
        menu.addAction('Nâng cao / Nhật ký', self._pulse_advanced)
        menu.addAction('Giao diện sáng / tối', self._on_toggle_theme)
        menu.addAction('Kiểm tra cập nhật', lambda: self.check_for_updates(manual=True))
        menu.addAction('Giới thiệu', self.show_about)
        menu.exec(self.cursor().pos())

    def _pulse_advanced(self):
        self._pulse_stack.setCurrentIndex(1)

    def _pulse_choose_file(self):
        if self._operation_state().is_hardware_busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Chọn chương trình', '', 'Application HEX (*.hex)')
        if path:
            self.program_view.set_file_path(Path(path))
            self._pulse_render()
            if self.image_info is None:
                self.pulse_view.set_status('Tệp chương trình không hợp lệ. Chọn lại tệp Application HEX.', error=True)
            else:
                self.pulse_view.set_status('Đã chọn chương trình. Bấm Nạp code để kiểm tra và xác nhận.')

    def _machine_setup_ready(self):
        super()._machine_setup_ready()
        if hasattr(self, 'pulse_view'):
            self.pulse_view.set_status('Thiết lập máy hoàn tất. Sẵn sàng sử dụng.')

    def _pulse_action(self, mode):
        if mode not in {'debug', 'program'} or self._operation_state().is_hardware_busy or self._pulse_close_requested:
            return
        if mode == 'program':
            self.pulse_view.set_mode('program')
            self.app_context.select_connection('local')
            path = self.program_view._selected_file
            if path is None:
                self._pulse_choose_file()
                path = self.program_view._selected_file
            if path is None:
                return
            if not self.openocd_ready:
                self.pulse_view.set_status('Bấm Thiết lập máy trước khi nạp.', error=True)
                return
            self._on_v18_flash_application(path, False)
            if not self._threads:
                self._pulse_program_error()
            return
        project = self.app_context.selected_project
        if project is None:
            self.pulse_view.set_status('Thêm dự án để chọn thư mục và tệp debug.', error=True)
            self._open_project_manager()
            return
        gateway = self.app_context.selected_connection.gateway
        if gateway is None:
            self._on_v18_open_local_vscode(project.workspace, project.symbols)
            return
        request = self._pulse_request(gateway, project)
        self._show_remote_login(request, launch_after=True)

    @staticmethod
    def _pulse_request(gateway, project=None):
        request = dict(host=gateway.endpoint.host, user=gateway.endpoint.user,
                       ssh_port=gateway.endpoint.port, gateway_id=gateway.profile_id)
        if project:
            request.update(workspace=project.workspace, elf=project.symbols, local_gdb_port=0)
        return request

    def _pulse_login(self, launch_after=False, selected=None):
        if self._operation_state().is_hardware_busy:
            return
        profiles = self._gateway_store.list()
        current = selected or self.app_context.selected_connection.gateway
        if current is not None and not any(item.profile_id == current.profile_id for item in profiles):
            profiles = tuple(profiles) + (current,)
        dialog = PulseLoginDialog(profiles, current.profile_id if current else None, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        try:
            profile, secret = dialog.profile(), dialog.password_input.text()
        finally:
            dialog.password_input.clear()
            dialog.deleteLater()
        self._pulse_connect(profile, secret, launch_after=launch_after)

    def _pulse_connect(self, profile, secret, *, launch_after=False):
        if self._operation_state().is_hardware_busy:
            return
        def connected(state):
            session = self._gateway_sessions.session(profile.endpoint)
            self._gateway_store.upsert(profile, make_default=True)
            self._refresh_shared_profiles()
            self.app_context.select_connection(profile.profile_id if profile.profile_id != 'local' else 'gateway:local')
            self._vscode_remote_session = session
            self.debug_vscode_view.set_client_connection_status(True, session.endpoint)
            self._sync_gateway_health_binding()
            self.pulse_view.set_status('Đã đăng nhập SSH.')
            if launch_after:
                project = self.app_context.selected_project
                if project is not None:
                    self._launch_remote_debug(self._pulse_request(profile, project), session)
        self._start_pulse_task(
            'connect', lambda: self._gateway_sessions.connect(profile.endpoint, secret or None, timeout_seconds=12),
            connected, 'Đang đăng nhập SSH…',
        )

    def _show_remote_login(self, request, *, launch_after):
        if self._operation_state().is_hardware_busy:
            return
        try:
            endpoint = self._profile_from_request(request)
            profile = self._gateway_profile_for_endpoint(endpoint)
            if self._gateway_sessions.connected(endpoint):
                session = self._gateway_sessions.session(endpoint)
                if launch_after:
                    self._launch_remote_debug(request, session)
                else:
                    self.pulse_view.set_status('Đã đăng nhập SSH.')
            else:
                self._pulse_login(launch_after=launch_after, selected=profile)
        except Exception as error:
            self._pulse_error(error)

    def _on_v18_open_local_vscode(self, workspace, elf):
        if self._operation_state().is_hardware_busy or not self.app_context.selected_connection.is_local:
            return
        try:
            probe = self._selected_debug_probe()
        except Exception as error:
            self._pulse_error(error)
            return
        self._start_pulse_debug(workspace, lambda force: self._vscode_controller.start_local(
            probe=probe, workspace=workspace, symbols=elf, force_launch_json=force))

    def _launch_remote_debug(self, request, session):
        workspace, elf = Path(request.get('workspace', '')), Path(request.get('elf', ''))
        self._start_pulse_debug(workspace, lambda force: self._vscode_controller.start_client(
            session=session, workspace=workspace, symbols=elf,
            profile_id=str(request.get('gateway_id', '')),
            local_gdb_port=int(request.get('local_gdb_port', 0)), force_launch_json=force))

    def _start_pulse_debug(self, workspace, operation, force=False):
        def completed(result):
            self._render_bridge_state()
            self.pulse_view.set_status('VS Code đã mở. Nhấn F5 trong VS Code để bắt đầu debug.')
        def failed(error):
            self._render_bridge_state()
            if isinstance(error, FileExistsError):
                if not force and self._confirm_launch_overwrite(workspace):
                    self._start_pulse_debug(workspace, operation, force=True)
                else:
                    self.pulse_view.set_status('Đã hủy thay cấu hình debug.')
            else:
                self._pulse_error(error)
        self._start_pulse_task('debug', lambda: operation(force), completed,
                               'Đang chuẩn bị debug…', failed)

    def _on_v18_start_gateway(self):
        if self._operation_state().is_hardware_busy:
            return
        try:
            probe = self._selected_debug_probe()
        except Exception as error:
            self._pulse_error(error)
            return
        self._start_pulse_task('gateway', lambda: self._vscode_controller.start_gateway(probe=probe),
                               lambda result: self._render_bridge_state(), 'Đang chuẩn bị kết nối…')

    def _on_v18_stop_bridge(self):
        if self._pulse_tasks.busy or self._threads:
            return
        def stopped(result):
            self._render_bridge_state()
            self.pulse_view.set_status('Đã dừng debug.')
        self._start_pulse_task('stop', self._vscode_controller.stop, stopped, 'Đang dừng debug…')

    def _start_pulse_task(self, name, operation, completed, status='', failed=None):
        if self._pulse_tasks.busy or self._pulse_close_requested:
            return False
        self._pulse_handlers[name] = (completed, failed)
        if status:
            self.pulse_view.set_status(status)
        return self._pulse_tasks.start(name, operation)

    def _pulse_busy_changed(self, busy):
        if busy:
            self._gateway_health.stop()
        self._update_controls()

    def _pulse_completed(self, name, result):
        completed, failed = self._pulse_handlers.pop(name, (None, None))
        if name == 'close':
            self._pulse_cleanup_done = True
            QTimer.singleShot(0, self.close)
            return
        if self._pulse_close_requested:
            self._pulse_begin_close()
            return
        try:
            if completed:
                completed(result)
        except Exception as error:
            self._pulse_error(error)
        self._pulse_drain_jobs()

    def _pulse_failed(self, name, error):
        completed, failed = self._pulse_handlers.pop(name, (None, None))
        if name == 'close':
            self._pulse_close_requested = False
            self._pulse_error(error)
            return
        if self._pulse_close_requested:
            self._pulse_begin_close()
            return
        if failed:
            failed(error)
        else:
            self._pulse_error(error)
        self._pulse_drain_jobs()

    def _pulse_error(self, error):
        message = str(error).strip() or type(error).__name__
        self.append_log('Pulse: ' + message)
        self.pulse_view.set_status(message, error=True)
        self._render_bridge_state()

    def _schedule_lifecycle(self, operation):
        # Called by the controller's GUI dispatcher. Never race a new start.
        if self._pulse_close_requested:
            return
        self._pulse_jobs.append(operation)
        self._pulse_drain_jobs()

    def _pulse_drain_jobs(self):
        if self._pulse_tasks.busy or self._pulse_close_requested:
            return
        if self._pulse_jobs:
            operation = self._pulse_jobs.popleft()
            self._start_pulse_task('lifecycle', operation, lambda _: self._render_bridge_state())
        else:
            self._sync_gateway_health_binding()

    def _sync_gateway_health_binding(self, *_):
        tasks = getattr(self, '_pulse_tasks', None)
        if self._pulse_close_requested or (tasks and tasks.busy):
            return
        super()._sync_gateway_health_binding()

    def _on_gateway_recovered(self, snapshot):
        self._on_gateway_snapshot(snapshot)
        if not hasattr(self, '_pulse_tasks'):
            return
        state = self._vscode_controller.state
        connection, project = self.app_context.selected_connection, self.app_context.selected_project
        gateway = connection.gateway
        if gateway is None or project is None or state.role != DebugRole.CLIENT:
            return
        token = self._vscode_controller._lease_token
        revision = self._pulse_selection_revision
        def synchronize():
            if (token != self._vscode_controller._lease_token
                    or revision != self._pulse_selection_revision):
                return
            return self._vscode_controller.synchronize_client(
                session=self._gateway_sessions.session(gateway.endpoint), workspace=project.workspace,
                symbols=project.symbols, gateway_snapshot=snapshot, profile_id=gateway.profile_id)
        self._schedule_lifecycle(synchronize)

    def _set_status(self, text, state, *, notify=True):
        super()._set_status(text, state, notify=notify and not hasattr(self, 'pulse_view'))
        if hasattr(self, 'pulse_view') and state == 'error':
            self.pulse_view.set_status(text, error=True)

    def _pulse_program_error(self):
        banner = self.program_view.banner
        self.pulse_view.set_status(banner.title_label.text() + '. ' + banner.detail_label.text(),
                                   error=banner.property('variant') == 'fail')

    def _worker_finished(self):
        super()._worker_finished()
        if hasattr(self, 'pulse_view') and self.pulse_view.mode == 'program' and not self._threads:
            self._pulse_program_error()

    def _flash_phase_changed(self, event):
        super()._flash_phase_changed(event)
        if hasattr(self, 'pulse_view'):
            self.pulse_view.set_status('Đang nạp chương trình… %d%%' % event.progress)

    def _flash_finished(self, result):
        super()._flash_finished(result)
        self.pulse_view.set_status('Đã nạp và xác minh chương trình.' if result.succeeded else result.reason,
                                   error=not result.succeeded)

    def closeEvent(self, event):
        if not hasattr(self, '_pulse_tasks'):
            return super().closeEvent(event)
        if self._threads or self.busy:
            # Preserve the canonical non-cancellable programming guard before
            # V18 has a chance to tear down sessions.
            return MainWindow.closeEvent(self, event)
        if self._pulse_cleanup_done:
            self._update_poll_timer.stop()
            event.accept()
            return
        event.ignore()
        self._pulse_close_requested = True
        self.pulse_view.set_status('Đang đóng kết nối an toàn…')
        self._gateway_health.stop()
        if not self._pulse_tasks.busy:
            self._pulse_begin_close()

    def _pulse_begin_close(self):
        if self._pulse_tasks.busy:
            return
        if not self.monitor_view.prepare_symbol_shutdown() or not self.monitor_view.controller.prepare_shutdown():
            self._pulse_close_requested = False
            self.pulse_view.set_status('Đang dừng tác vụ. Đóng lại sau khi tác vụ hoàn tất.')
            return
        health_workers = tuple(self._gateway_health.findChildren(QThread))
        self._pulse_jobs.clear()
        def cleanup():
            try:
                for worker in health_workers:
                    worker.cancel()
                    if not worker.wait(12000):
                        raise RuntimeError('Kết nối SSH chưa dừng. Chờ tác vụ hoàn tất rồi đóng lại.')
            finally:
                try:
                    self._vscode_controller.stop()
                finally:
                    self._gateway_sessions.disconnect_all()
        self._pulse_tasks.start('close', cleanup)
