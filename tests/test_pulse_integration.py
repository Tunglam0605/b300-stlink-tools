"""Production Pulse wiring checks; no USB, network or target execution."""
from __future__ import annotations
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication
from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.gateway_sessions import GatewaySessionManager
from b300_core.project_profiles import ProjectProfile, ProjectProfileStore
from b300_core.vscode_bridge import BridgeState, DebugRole, VsCodeBridgeState
from b300_gui.workers import FunctionWorker
try:
    from b300_gui.main_window_pulse import MainWindowPulse
except ImportError:
    MainWindowPulse = None


class PulseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.assertIsNotNone(MainWindowPulse, 'Production Pulse window must exist')
        self.temp = tempfile.TemporaryDirectory()
        # Windows CI may provide an 8.3 TEMP path; profiles canonicalize paths.
        self.root = Path(self.temp.name).resolve()
        self.projects = ProjectProfileStore(self.root / 'projects.json')
        self.gateways = GatewayProfileStore(self.root / 'gateways.json', legacy_path=self.root/'legacy.json')
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.symbols = self.workspace / 'app.elf'
        self.symbols.write_bytes(b'ELF')
        self.project = ProjectProfile.create('Board', self.workspace, self.symbols)
        self.projects.upsert(self.project, make_default=True)
        self.gateway = GatewayProfile.create('IPC', 'ipc.example', 'engineer')
        self.gateways.upsert(self.gateway, make_default=True)
        self.window = MainWindowPulse(
            probe_loader=lambda: (), automatic_updates=False, first_run_setup=False,
            project_store=self.projects, gateway_store=self.gateways,
            gateway_sessions=GatewaySessionManager(),
            settings=QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat),
        )
        self.window._gateway_health._worker_factory = None

    def wait_for(self, predicate):
        end = time.monotonic() + 4
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate())

    def tearDown(self):
        if getattr(self, 'window', None):
            self.window.close()
            self.wait_for(lambda: self.window._pulse_cleanup_done)
            self.app.processEvents()
            self.window.deleteLater()
            self.app.processEvents()
        if getattr(self, 'temp', None):
            self.temp.cleanup()

    def test_default_pulse_and_setup_use_real_window(self):
        from b300_gui.__main__ import MainWindow
        self.assertIs(MainWindow, MainWindowPulse)
        self.assertEqual(self.window.pulse_view.mode, 'debug')
        self.assertIs(self.window.app_context.selected_project, self.window.app_context.project_profiles[0])
        self.assertEqual(self.window.app_context.selected_connection.gateway, self.gateway)
        with mock.patch.object(self.window, 'show_machine_setup') as setup:
            self.window.pulse_view.setup_requested.emit()
            setup.assert_called_once()
        with mock.patch.object(self.window, '_on_v18_flash_application') as flash:
            self.window.program_view._selected_file = self.workspace / 'application.hex'
            self.window.openocd_ready = True
            self.window._pulse_action('program')
            flash.assert_called_once_with(self.workspace / 'application.hex', False)
        self.assertTrue(self.window.app_context.selected_connection.is_local)

    def test_advanced_surface_preserves_production_workspaces_and_remote_programming(self):
        self.assertIsNotNone(self.window._remote_program_history)
        self.assertTrue(callable(self.window._begin_remote_application_program))
        self.window._pulse_advanced()
        advanced = self.window._pulse_stack.currentWidget()
        self.assertEqual(self.window._pulse_stack.currentIndex(), 1)
        for view in (self.window.program_view, self.window.monitor_view,
                     self.window.debug_vscode_view):
            self.assertTrue(advanced.isAncestorOf(view))

    def test_debug_button_passes_saved_project_and_gateway_to_backend_in_worker(self):
        captured, thread_ids = [], []
        result = mock.Mock()
        result.state = VsCodeBridgeState(DebugRole.CLIENT, BridgeState.READY, '127.0.0.1:43123')
        result.symbols = self.symbols
        def start(**kwargs):
            captured.append(kwargs)
            thread_ids.append(threading.get_ident())
            return result
        controller = self.window._vscode_controller
        with mock.patch.object(self.window._gateway_sessions, 'connected', return_value=True), \
             mock.patch.object(controller, 'start_client', side_effect=start):
            self.window.pulse_view.debug_start.click()
            self.wait_for(lambda: not self.window._pulse_tasks.busy)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['workspace'], self.workspace)
        self.assertEqual(captured[0]['symbols'], self.symbols)
        self.assertEqual(captured[0]['profile_id'], self.gateway.profile_id)
        self.assertEqual(captured[0]['local_gdb_port'], 0)
        self.assertNotEqual(thread_ids[0], threading.get_ident())

    def test_program_navigation_restores_last_debug_connection_including_local(self):
        view = self.window.pulse_view
        for selected in (self.gateway.profile_id, 'local'):
            with self.subTest(connection=selected):
                self.window.app_context.select_connection(selected)
                view.set_mode('program')
                self.assertTrue(self.window.app_context.selected_connection.is_local)
                view.set_mode('debug')
                self.assertEqual(self.window.app_context.selected_connection.connection_id, selected)

    def test_debug_button_uses_selected_local_connection_without_switching_to_ipc(self):
        self.window.app_context.select_connection('local')
        captured = []
        controller = self.window._vscode_controller
        with mock.patch.object(self.window, '_selected_debug_probe', return_value=mock.Mock()), \
             mock.patch.object(controller, 'start_local', side_effect=lambda **kw: captured.append(kw)):
            self.window.pulse_view.debug_start.click()
            self.wait_for(lambda: not self.window._pulse_tasks.busy)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['workspace'], self.workspace)
        self.assertEqual(captured[0]['symbols'], self.symbols)
        self.assertTrue(self.window.app_context.selected_connection.is_local)

    def test_failed_connect_never_reports_ready_and_secret_not_persisted(self):
        with mock.patch.object(self.window._gateway_sessions, 'connect', side_effect=RuntimeError('SSH refused')):
            self.window._pulse_connect(self.gateway, 'private-test-secret')
            self.wait_for(lambda: not self.window._pulse_tasks.busy)
        self.assertIn('SSH refused', self.window.pulse_view.status.text())
        self.assertNotIn('private-test-secret', self.gateways.path.read_text())
        self.assertFalse(self.window._gateway_sessions.connected(self.gateway.endpoint))

    def test_busy_selection_and_close_wait_for_task_without_teardown_race(self):
        gate, calls = threading.Event(), []
        self.window._pulse_tasks.start('connect-test', lambda: gate.wait(2))
        self.assertTrue(self.window.app_context.hardware_busy)
        self.assertFalse(self.window.app_context.select_connection('local'))
        event = QCloseEvent()
        with mock.patch.object(self.window._vscode_controller, 'stop', side_effect=lambda: calls.append('stop')):
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertEqual(calls, [])
            gate.set()
            self.wait_for(lambda: self.window._pulse_cleanup_done)
        self.assertEqual(calls, ['stop'])

    def test_launch_conflict_requires_confirmation_and_no_blind_retry(self):
        self.window.app_context.select_connection('local')
        with mock.patch.object(self.window, '_selected_debug_probe', return_value=mock.Mock()), \
             mock.patch.object(self.window._vscode_controller, 'start_local', side_effect=FileExistsError('conflict')) as start, \
             mock.patch.object(self.window, '_confirm_launch_overwrite', return_value=False) as confirm:
            self.window._on_v18_open_local_vscode(self.workspace, self.symbols)
            self.wait_for(lambda: bool(confirm.call_count))
        start.assert_called_once()
        confirm.assert_called_once_with(self.workspace)

    def test_close_failure_surfaces_error_and_allows_manual_close_again(self):
        with mock.patch.object(self.window._vscode_controller, 'stop', side_effect=RuntimeError('cleanup failed')), \
             mock.patch.object(self.window._gateway_sessions, 'disconnect_all') as disconnect:
            self.window.close()
            self.wait_for(lambda: not self.window._pulse_tasks.busy)
            self.assertFalse(self.window._pulse_cleanup_done)
            self.assertFalse(self.window._pulse_close_requested)
            self.assertIn('cleanup failed', self.window.pulse_view.status.text())
            disconnect.assert_called_once()

    def test_queued_recovery_cannot_rebind_after_project_selection_changes(self):
        controller = self.window._vscode_controller
        controller.bridge = mock.Mock()
        controller.bridge.state = VsCodeBridgeState(DebugRole.CLIENT, BridgeState.FAILED, None)
        controller._lease_token = 'old-lease'
        queued = []
        with mock.patch.object(self.window, '_schedule_lifecycle', side_effect=queued.append), \
             mock.patch.object(self.window, '_on_gateway_snapshot'), \
             mock.patch.object(controller, 'synchronize_client') as sync:
            self.window._on_gateway_recovered(mock.Mock())
            another = ProjectProfile.create('Other', self.workspace, self.symbols)
            # Simulate an externally refreshed selection while recovery is queued.
            self.window.app_context.selected_project = another
            self.window.app_context.changed.emit()
            queued.pop()()
            sync.assert_not_called()
        controller.bridge = mock.Mock()
        controller.bridge.state = VsCodeBridgeState(None, BridgeState.STOPPED, None)

    def test_failed_bridge_keeps_selection_locked_until_stop(self):
        controller = self.window._vscode_controller
        bridge = controller.bridge
        controller.bridge = mock.Mock()
        controller.bridge.state = VsCodeBridgeState(DebugRole.CLIENT, BridgeState.FAILED, None)
        controller._lease_token = 'active-lease'
        try:
            self.window._update_controls()
            self.assertTrue(self.window.app_context.hardware_busy)
            self.assertFalse(self.window.app_context.select_connection('local'))
            self.assertFalse(self.window.pulse_view.stop_button.isHidden())
            self.assertTrue(self.window.pulse_view.stop_button.isEnabled())
        finally:
            controller.bridge, controller._lease_token = bridge, None

    def test_close_waits_for_real_health_thread_and_clears_sessions(self):
        entered, release = threading.Event(), threading.Event()
        def poll(log, phase, cancel):
            entered.set()
            release.wait(2)
        worker = FunctionWorker(poll, self.window._gateway_health)
        worker.start()
        entered.wait(1)
        calls = []
        try:
            with mock.patch.object(self.window._gateway_sessions, 'disconnect_all', side_effect=lambda: calls.append('disconnect')):
                self.window.close()
                self.app.processEvents()
                self.assertFalse(self.window._pulse_cleanup_done)
                self.assertEqual(calls, [])
                release.set()
                self.wait_for(lambda: self.window._pulse_cleanup_done)
            self.assertEqual(calls, ['disconnect'])
        finally:
            release.set()
            worker.wait(2000)

    def test_health_state_updates_do_not_restyle_the_whole_window(self):
        with mock.patch('b300_gui.production_window.apply_reference_palette') as apply:
            for _ in range(3):
                self.window._update_controls()
            apply.assert_not_called()

    def test_health_timeout_still_releases_bridge_and_ssh(self):
        health_worker = mock.Mock()
        health_worker.wait.return_value = False
        with mock.patch.object(self.window._gateway_health, 'findChildren', return_value=[health_worker]), \
             mock.patch.object(self.window._vscode_controller, 'stop') as stop, \
             mock.patch.object(self.window._gateway_sessions, 'disconnect_all') as disconnect:
            self.window.close()
            self.wait_for(lambda: not self.window._pulse_tasks.busy)
            self.assertFalse(self.window._pulse_cleanup_done)
            self.assertIn('SSH', self.window.pulse_view.status.text())
            stop.assert_called_once()
            disconnect.assert_called_once()
