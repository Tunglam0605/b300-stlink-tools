from __future__ import annotations

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import unittest
from unittest.mock import Mock
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

try:
    from b300_gui.pulse_tasks import PulseTasks
except ImportError:
    PulseTasks = None

from b300_core.vscode_bridge import BridgeState, DebugRole, VsCodeBridgeState
from b300_gui.vscode_debug_controller import GuiDispatcher, VsCodeDebugController


class PulseTasksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate):
        end = time.monotonic() + 3
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate())

    def test_tasks_are_serial_and_ui_remains_responsive(self):
        self.assertIsNotNone(PulseTasks, 'Production async task adapter is required')
        tasks = PulseTasks()
        gate = threading.Event()
        outcomes, ticks = [], []
        tasks.completed.connect(lambda name, result: outcomes.append((name, result, tasks.busy)))
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        try:
            self.assertTrue(tasks.start('connect', lambda: gate.wait(2) and 'ready'))
            self.assertFalse(tasks.start('duplicate', lambda: 'wrong'))
            self.wait_for(lambda: len(ticks) > 2)
            self.assertTrue(tasks.busy)
            self.assertEqual(outcomes, [])
        finally:
            gate.set()
            self.wait_for(lambda: not tasks.busy)
            timer.stop()
        self.assertEqual(outcomes, [('connect', 'ready', False)])

    def test_failure_preserves_exception_and_can_start_again(self):
        self.assertIsNotNone(PulseTasks)
        tasks = PulseTasks()
        failures, outcomes = [], []
        tasks.failed.connect(lambda name, error: failures.append((name, error, tasks.busy)))
        tasks.completed.connect(lambda name, result: outcomes.append(result))
        error = FileExistsError('launch.json conflict')
        def fail():
            raise error
        tasks.start('debug', fail)
        self.wait_for(lambda: bool(failures))
        self.assertEqual(failures, [('debug', error, False)])
        self.assertTrue(tasks.start('stop', lambda: 'stopped'))
        self.wait_for(lambda: bool(outcomes))
        self.assertEqual(outcomes, ['stopped'])

    def test_debug_publish_and_release_are_dispatched_in_order(self):
        context, dispatcher = Mock(), Mock()
        queued = []
        dispatcher.submit.side_effect = queued.append
        controller = VsCodeDebugController(context=context, ui_dispatcher=dispatcher)
        state = VsCodeBridgeState(DebugRole.CLIENT, BridgeState.READY, '127.0.0.1:3456')
        controller._publish_debug(state)
        token = controller._lease_token
        context.apply_device_state.assert_not_called()
        queued.pop()()
        controller._release_debug('stopped')
        self.assertEqual(context.apply_device_state.call_count, 1)
        queued.pop()()
        first, second = context.apply_device_state.call_args_list
        self.assertEqual(first.kwargs['owner_kind'], 'DEBUGGING')
        self.assertEqual(first.kwargs['lease_token'], token)
        self.assertIsNone(second.kwargs['owner_kind'])
        self.assertEqual(second.kwargs['lease_token'], token)

    def test_queued_ready_is_ignored_after_lifecycle_stops(self):
        context, dispatcher = Mock(), Mock()
        queued = []
        dispatcher.submit.side_effect = queued.append
        controller = VsCodeDebugController(context=context, ui_dispatcher=dispatcher)
        controller._publish_debug(VsCodeBridgeState(DebugRole.CLIENT, BridgeState.READY, '127.0.0.1:3333'))
        controller._release_debug('stopped')
        for callback in queued:
            callback()
        context.apply_device_state.assert_called_once()
        self.assertIsNone(context.apply_device_state.call_args.kwargs['owner_kind'])

    def test_detach_cleanup_uses_serial_lifecycle_lane(self):
        scheduled = []
        controller = VsCodeDebugController()
        bridge = Mock()
        bridge.stop_if_generation.return_value = VsCodeBridgeState(None, BridgeState.STOPPED, None)
        controller.bridge = bridge
        self.assertTrue(hasattr(controller, 'set_lifecycle_scheduler'))
        controller.set_lifecycle_scheduler(scheduled.append)
        controller._on_last_client_detached(4)
        bridge.stop_if_generation.assert_not_called()
        self.assertEqual(len(scheduled), 1)
        scheduled.pop()()
        bridge.stop_if_generation.assert_called_once_with(4)

    def test_stale_lease_loss_cannot_stop_new_bridge(self):
        scheduled = []
        controller = VsCodeDebugController()
        self.assertTrue(hasattr(controller, 'set_lifecycle_scheduler'))
        controller.set_lifecycle_scheduler(scheduled.append)
        controller.bridge = Mock()
        controller._lease_epoch = 1
        controller._on_gateway_lease_lost(1)
        controller._lease_epoch = 2
        scheduled.pop()()
        controller.bridge.stop.assert_not_called()

    def test_real_dispatcher_executes_worker_callback_on_gui_thread(self):
        dispatcher, observed = GuiDispatcher(), []
        gui_thread = threading.get_ident()
        worker = threading.Thread(target=lambda: dispatcher.submit(lambda: observed.append(threading.get_ident())))
        worker.start()
        worker.join()
        self.assertEqual(observed, [])
        self.wait_for(lambda: bool(observed))
        self.assertEqual(observed, [gui_thread])

    def test_stop_failure_still_releases_gateway_lease_and_context(self):
        context = Mock()
        controller = VsCodeDebugController(context=context)
        controller.bridge = Mock()
        controller.bridge.stop.side_effect = RuntimeError('bridge cleanup failed')
        lease = Mock()
        controller._gateway_lease_client = lease
        controller._lease_token = 'owned'
        with self.assertRaisesRegex(RuntimeError, 'bridge cleanup failed'):
            controller.stop()
        lease.close.assert_called_once()
        self.assertIsNone(controller._gateway_lease_client)
        self.assertIsNone(controller._lease_token)
        self.assertIsNone(context.apply_device_state.call_args.kwargs['owner_kind'])
