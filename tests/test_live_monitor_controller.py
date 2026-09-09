from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_core.live_monitor import LiveSample, LiveValue, LiveWatch
from b300_core.models import ProbeRef
from b300_core.offline_symbols import SourceLocation
from b300_core.remote_profile import RemoteGatewayProfile
from b300_gui import live_monitor_controller
from b300_gui.debug_live_panel import DebugLivePanel
from b300_gui.live_monitor_controller import LiveMonitorController
from b300_gui.views.monitor_view import MonitorView

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget


class _Signal:
    def __init__(self) -> None:
        self._receivers = []

    def connect(self, receiver) -> None:
        self._receivers.append(receiver)

    def emit(self, value=None) -> None:
        for receiver in tuple(self._receivers):
            if value is None:
                receiver()
            else:
                receiver(value)


class _InlineWorker:
    def __init__(self, operation, _parent=None) -> None:
        self.operation = operation
        self.log = _Signal()
        self.phase = _Signal()
        self.completed = _Signal()
        self.failed = _Signal()
        self.finished = _Signal()
        self.cancelled = False
        self.cancel_event = threading.Event()
        self.deleted = False

    def start(self) -> None:
        result = self.operation(self.log.emit, self.phase.emit, self.cancel_event)
        self.completed.emit(result)
        self.finished.emit()

    def cancel(self) -> None:
        self.cancelled = True
        self.cancel_event.set()

    def isRunning(self) -> bool:
        return False

    def wait(self, _milliseconds: int) -> bool:
        return True

    def deleteLater(self) -> None:
        self.deleted = True


class _Panel:
    def __init__(self) -> None:
        self.interval = SimpleNamespace(value=lambda: 0.5)
        self.samples = []
        self.analytics = []
        self.completed = []
        self.failures = []
        self.control_states = []
        self.reset_count = 0

    def watch_specs(self):
        return ("speed:f32",)

    def compiled_watches(self):
        return ()

    def sample_limit(self):
        return 2

    def reset_for_sampling(self) -> None:
        self.reset_count += 1

    def append_live_sample(self, sample) -> None:
        self.samples.append(sample)

    def apply_analytics(self, analytics) -> None:
        self.analytics.append(analytics)

    def mark_live_completed(self, summary) -> None:
        self.completed.append(summary)

    def mark_failed(self, message) -> None:
        self.failures.append(message)

    def set_control_state(self, *, start_enabled, stop_enabled, history_enabled) -> None:
        self.control_states.append((start_enabled, stop_enabled, history_enabled))


class _Session:
    def __init__(self, samples) -> None:
        self.samples = tuple(samples)
        self.started_config = None
        self.closed = False
        self.cancelled = False

    def start_local(self, config):
        self.started_config = config
        return SimpleNamespace(
            role="local", transport="swd-tcl-loopback",
            tcl_endpoint="127.0.0.1:6666", initial_target_state="running",
        )

    def run(self, on_sample):
        for sample in self.samples:
            on_sample(sample)
        return SimpleNamespace(
            samples=len(self.samples), overruns=0,
            final_target_state="running", cancelled=False,
        )

    def analytics_snapshot(self):
        return SimpleNamespace(functions=(), timing=SimpleNamespace())

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


class LiveMonitorControllerTests(unittest.TestCase):
    def _restart_fixture(self, directory, *, worker_wait=True):
        class ControlledWorker(_InlineWorker):
            def __init__(self, operation, parent=None):
                super().__init__(operation, parent); self.running = False; self.wait_result = worker_wait
            def start(self):
                self.running = True
                self.result = self.operation(self.log.emit, self.phase.emit, self.cancel_event)
            def isRunning(self): return self.running
            def wait(self, _milliseconds):
                if self.wait_result: self.running = False
                return self.wait_result
        class ControlledSession(_Session):
            def __init__(self): super().__init__(()); self.cancel_calls = 0
            def cancel(self): self.cancel_calls += 1
            def start_client(self, config, remote_session=None):
                self.remote_session = remote_session
                return self.start_local(config)
        sessions, workers = [], []
        def make_session(**_kwargs):
            item = ControlledSession(); sessions.append(item); return item
        def make_worker(operation, parent=None):
            item = ControlledWorker(operation, parent); workers.append(item); return item
        symbols = Path(directory) / "application.axf"; symbols.write_bytes(b"ELF")
        remote = SimpleNamespace(gateway_status=lambda: None)
        binding = SimpleNamespace(tcl_endpoint="127.0.0.1:42001")
        coordinator = SimpleNamespace(binding=binding, close=lambda: None)
        controller = LiveMonitorController(
            _Panel(), remote_session_provider=lambda _request: remote,
            session_factory=make_session, worker_factory=make_worker,
        )
        old = ControlledWorker(lambda *_args: None); old.running = True
        old_session = ControlledSession()
        controller._active = True; controller._worker = old; controller._live_session = old_session
        controller._gateway_coordinator = coordinator; controller._gateway_binding = object()
        controller._last_request = live_monitor_controller.LiveMonitorRequest.client(
            symbols, host="gateway.local", user="operator", profile_id="lab",
        )
        controller._last_symbols_revision = (symbols.stat().st_size, symbols.stat().st_mtime_ns)
        controller._last_symbols_digest = controller._sha256(symbols)
        return controller, coordinator, binding, old, old_session, sessions, workers, symbols

    def test_changed_ready_binding_restarts_one_client_worker_on_same_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, coordinator, binding, old, old_session, sessions, workers, _symbols = self._restart_fixture(directory)
            state = SimpleNamespace(state="READY", binding=binding)
            controller._restart_client_binding(coordinator, state)
            self.assertTrue(old.cancelled); self.assertTrue(old.deleted)
            self.assertEqual(old_session.cancel_calls, 1)
            self.assertEqual((len(sessions), len(workers)), (1, 1))
            self.assertTrue(controller.active)
            self.assertEqual(sessions[0].started_config.bound_tcl_endpoint, "127.0.0.1:42001")
            self.assertIs(controller._gateway_coordinator, coordinator)
            self.assertFalse(old.isRunning())
            self.assertTrue(workers[0].isRunning())

    def test_restart_timeout_stays_stale_without_second_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, coordinator, binding, _old, _old_session, sessions, workers, _symbols = self._restart_fixture(directory, worker_wait=False)
            controller._restart_client_binding(coordinator, SimpleNamespace(state="READY", binding=binding))
            self.assertEqual((sessions, workers), ([], []))
            self.assertIn("MONITOR_RESTART_TIMEOUT", controller._invalidated_reason)

    def test_restart_refuses_changed_axf_without_second_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, coordinator, binding, _old, _old_session, sessions, workers, symbols = self._restart_fixture(directory)
            symbols.write_bytes(b"ELF changed")
            controller._restart_client_binding(coordinator, SimpleNamespace(state="READY", binding=binding))
            self.assertEqual((sessions, workers), ([], []))
            self.assertIn("MONITOR_RESTART_AXF_CHANGED", controller._invalidated_reason)

    def test_old_epoch_finished_failed_and_sample_cannot_overwrite_restarted_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, coordinator, binding, _old, _old_session, _sessions, workers, _symbols = self._restart_fixture(directory)
            old_epoch = controller._epoch
            controller._restart_client_binding(coordinator, SimpleNamespace(state="READY", binding=binding))
            controller._worker_finished_for_epoch(old_epoch)
            controller._failed_for_epoch(old_epoch, SimpleNamespace(message="old failure"))
            sample = LiveSample(1, .1, .1, .01, False, 0x08010000,
                                SourceLocation(0x08010000, "main", "main.c", 1), ())
            controller._sample_received((sample, old_epoch, object()))
            self.assertTrue(controller.active)
            self.assertIs(controller._worker, workers[0])
    def test_late_sample_from_superseded_gateway_binding_is_rejected(self) -> None:
        panel = _Panel()
        controller = LiveMonitorController(panel)
        controller._active = True
        accepted = []
        controller._gateway_coordinator = SimpleNamespace(accept_sample=lambda binding: accepted.append(binding) or False)
        controller._gateway_binding = object()
        sample = LiveSample(
            cycle=1, scheduled_elapsed_seconds=.1, captured_elapsed_seconds=.1,
            read_duration_seconds=.01, overrun=False, pc=0x08010000,
            source=SourceLocation(0x08010000, "main", "main.c", 1), values=(),
        )
        controller._sample_received((sample, object()))
        self.assertEqual(len(accepted), 1)
        self.assertEqual(panel.samples, [])

    def test_changed_gateway_binding_invalidates_active_monitor_with_actionable_reason(self) -> None:
        panel = _Panel()
        panel.mark_stale = lambda reason: panel.failures.append(reason)
        controller = LiveMonitorController(panel)
        controller._active = True
        controller._gateway_binding = object()
        controller._gateway_coordinator = SimpleNamespace(
            health=lambda: SimpleNamespace(state="STALE", binding=None,
                                           reason_code="GATEWAY_RECOVERY_EXHAUSTED",
                                           next_action="Reconnect then retry."),
        )
        state = controller.check_gateway_health()
        self.assertEqual(state.reason_code, "GATEWAY_RECOVERY_EXHAUSTED")
        self.assertIn("GATEWAY_RECOVERY_EXHAUSTED", controller._invalidated_reason)
        self.assertIn("Reconnect then retry", panel.failures[-1])

    def test_old_worker_completion_is_ignored_after_epoch_advance(self) -> None:
        panel = _Panel()
        controller = LiveMonitorController(panel)
        controller._epoch = 2
        controller._active = True
        controller._completed_for_epoch(1, SimpleNamespace(
            samples=1, overruns=0, final_target_state="running", cancelled=False,
        ))
        self.assertTrue(controller.active)
        self.assertEqual(panel.completed, [])

    def test_gateway_snapshot_queues_recovery_without_inline_ssh_and_late_result_is_ignored(self):
        workers = []
        class Deferred:
            def __init__(self, operation, _parent=None):
                self.operation = operation; self.completed = _Signal(); self.failed = _Signal(); self.finished = _Signal(); self.started = False; self.deleted = False
            def start(self): self.started = True
            def cancel(self): pass
            def deleteLater(self): self.deleted = True
        panel = _Panel(); panel.mark_stopping = lambda: None
        controller = LiveMonitorController(panel, recovery_worker_factory=lambda op, parent: workers.append(Deferred(op, parent)) or workers[-1])
        calls = []
        coordinator = SimpleNamespace(
            accept_health_snapshot=lambda _snapshot: SimpleNamespace(state="STALE", binding=None),
            health=lambda: calls.append("ssh") or SimpleNamespace(state="STALE", binding=None, reason_code="X", next_action="retry"),
        )
        controller._active = True; controller._epoch = 4; controller._gateway_coordinator = coordinator; controller._gateway_binding = object()
        controller.accept_gateway_health_snapshot(object())
        self.assertEqual(calls, [])
        self.assertEqual(len(workers), 1)
        state = workers[0].operation(None, None, None)
        controller.stop()
        workers[0].completed.emit(state)
        self.assertTrue(controller._active)

    def test_new_health_snapshot_during_recovery_is_coalesced_into_a_followup(self):
        workers = []
        class Deferred:
            def __init__(self, operation, _parent=None):
                self.operation = operation; self.completed = _Signal(); self.failed = _Signal(); self.finished = _Signal(); self.deleted = False
            def start(self): pass
            def cancel(self): pass
            def deleteLater(self): self.deleted = True
        panel = _Panel()
        controller = LiveMonitorController(
            panel, recovery_worker_factory=lambda op, parent: workers.append(Deferred(op, parent)) or workers[-1],
        )
        coordinator = SimpleNamespace(
            accept_health_snapshot=lambda _snapshot: SimpleNamespace(state="STALE", binding=None),
            health=lambda: SimpleNamespace(state="STALE", binding=None, reason_code="RETRY", next_action="retry"),
        )
        controller._active = True; controller._epoch = 7
        controller._gateway_coordinator = coordinator; controller._gateway_binding = object()
        controller.accept_gateway_health_snapshot(object())
        controller.accept_gateway_health_snapshot(object())
        self.assertEqual(len(workers), 1)
        first = workers[0]
        first.completed.emit(first.operation(None, None, None))
        first.finished.emit()
        self.assertEqual(len(workers), 2)

    def test_stop_intent_rejects_health_snapshot_recovery_before_worker_drains(self):
        workers = []
        panel = _Panel(); panel.mark_stopping = lambda: None
        controller = LiveMonitorController(panel, recovery_worker_factory=lambda op, parent: workers.append(op) or _InlineWorker(op, parent))
        coordinator = SimpleNamespace(accept_health_snapshot=lambda _value: (_ for _ in ()).throw(AssertionError("must not inspect after stop")))
        controller._active = True; controller._gateway_coordinator = coordinator
        controller.stop()
        controller.accept_gateway_health_snapshot(object())
        self.assertEqual(workers, [])

    def test_large_typed_batches_are_coalesced_before_rendering(self) -> None:
        """A fast 168-variable stream must not flood Qt with one render per batch."""
        from b300_gui.production_live_panel import ProductionLivePanel

        app = QApplication.instance() or QApplication([])
        panel = ProductionLivePanel()
        self.addCleanup(panel.deleteLater)
        controller = LiveMonitorController(panel)
        received = []
        panel.sample_received.connect(received.append)

        for batch_index in range(3):
            values = tuple(
                LiveValue(
                    "large.f%d" % (batch_index * 56 + index), "u8",
                    0x20000100 + batch_index * 56 + index, index, "%02X" % index,
                    node_id="large.f%d" % (batch_index * 56 + index),
                )
                for index in range(56)
            )
            controller._sample_received(LiveSample(
                cycle=batch_index,
                scheduled_elapsed_seconds=batch_index * 0.1,
                captured_elapsed_seconds=(batch_index + 1) * 0.1,
                read_duration_seconds=0.09,
                overrun=False,
                pc=0x08010000,
                source=SourceLocation(0x08010000, "main", "main.c", 1),
                values=values,
                batch_index=batch_index,
                batch_count=3,
            ))
        controller._sample_received(LiveSample(
            cycle=3, scheduled_elapsed_seconds=0.3, captured_elapsed_seconds=0.4,
            read_duration_seconds=0.09, overrun=False, pc=0x08010000,
            source=SourceLocation(0x08010000, "main", "main.c", 1),
            values=(LiveValue(
                "large.f0", "u8", 0x20000100, 99, "63", node_id="large.f0",
            ),), batch_index=0, batch_count=3,
        ))

        self.assertEqual(panel.table.rowCount(), 0)
        self.assertEqual(received, [])

        controller._flush_pending_samples()

        self.assertEqual(panel.table.rowCount(), 168)
        self.assertEqual(len(panel.buffer), 169)
        self.assertEqual(len(received), 4)
        self.assertEqual(sum(len(sample.values) for sample in received), 169)
        self.assertEqual(panel.table.item(panel.rows["large.f0"], 4).text(), "0.400")
        self.assertEqual(panel.table.item(panel.rows["large.f56"], 4).text(), "0.200")
        self.assertEqual(panel.table.item(panel.rows["large.f0"], 1).text(), "99")
        self.assertLessEqual(panel.recent_table.rowCount(), 50)
        app.processEvents()

    def test_stop_during_startup_survives_session_cancellation_reset(self) -> None:
        for role in ("LOCAL", "CLIENT"):
            with self.subTest(role=role):
                panel = _Panel()
                panel.mark_stopping = lambda: None

                class StartingSession(_Session):
                    def start_local(self, config):
                        info = super().start_local(config)
                        controller.stop()
                        # Real LiveMonitorSession clears its event after startup.
                        self.cancelled = False
                        return info

                    start_client = start_local

                    def run(self, on_sample):
                        if not self.cancelled:
                            on_sample(SimpleNamespace(cycle=0))
                        return SimpleNamespace(samples=0, overruns=0,
                                               final_target_state="running", cancelled=self.cancelled)

                session = StartingSession(())
                with tempfile.TemporaryDirectory() as directory:
                    symbols = Path(directory) / "application.axf"
                    symbols.write_bytes(b"ELF")
                    controller = LiveMonitorController(
                        panel, selected_probe=lambda: ProbeRef("probe"),
                        session_factory=lambda **_kwargs: session, worker_factory=_InlineWorker,
                    )
                    request = (live_monitor_controller.LiveMonitorRequest.local(symbols)
                               if role == "LOCAL" else live_monitor_controller.LiveMonitorRequest.client(
                                   symbols, host="gateway.local", user="operator"))
                    controller.start(request)
                self.assertEqual(panel.samples, [])
                self.assertTrue(panel.completed[0].cancelled)
                self.assertTrue(session.closed)
                self.assertFalse(controller.active)
                self.assertEqual(panel.control_states[-1], (True, False, True))

    def test_client_uses_authenticated_session_from_provider(self) -> None:
        authenticated = SimpleNamespace(
            ensure_gateway_ready=lambda: SimpleNamespace(
                attach_ready=True, tcl_endpoint="127.0.0.1:7666",
            )
        )
        received = []
        class ClientSession(_Session):
            def start_client(self, config, remote_session=None):
                received.append(remote_session)
                return self.start_local(config)
        session = ClientSession(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                _Panel(), remote_session_provider=lambda request: authenticated,
                session_factory=lambda **_kwargs: session, worker_factory=_InlineWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.client(
                symbols, host="gateway.local", user="operator"))
        self.assertEqual(received, [authenticated])
        self.assertTrue(session.closed)

    def test_monitor_start_publishes_shared_monitor_owner(self) -> None:
        from b300_gui.app_context import AppContext
        context = AppContext()
        panel = _Panel()
        session = _Session(())
        class DeferredWorker(_InlineWorker):
            def start(self) -> None:
                return
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel, context=context, selected_probe=lambda: ProbeRef("probe"),
                session_factory=lambda **_kwargs: session, worker_factory=DeferredWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))
        self.assertEqual(context.device_snapshot.owner_kind, "MONITORING")

    def test_monitor_completion_clears_live_target_evidence(self) -> None:
        from b300_gui.app_context import AppContext
        context = AppContext()
        context.apply_device_state(owner_kind="MONITORING", target_state="running")
        controller = LiveMonitorController(_Panel(), context=context)
        controller._active = True
        controller._finish_operation(history_enabled=True)
        state = context.device_snapshot
        self.assertIsNone(state.target_state)
        self.assertIsNone(state.checked_monotonic)
        self.assertEqual(state.liveness, "STALE")

    def test_client_asks_gateway_for_live_tcl_port_and_starts_it_before_monitoring(self) -> None:
        class GatewaySession:
            def __init__(self):
                self.ensure_calls = 0

            def ensure_gateway_ready(self):
                self.ensure_calls += 1
                return SimpleNamespace(
                    attach_ready=True, tcl_endpoint="127.0.0.1:7666",
                )

        class ClientSession(_Session):
            def start_client(self, config, remote_session=None):
                self.started_config = config
                self.remote_session = remote_session
                return self.start_local(config)

        gateway = GatewaySession()
        session = ClientSession(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                _Panel(), remote_session_provider=lambda _request: gateway,
                session_factory=lambda **_kwargs: session, worker_factory=_InlineWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.client(
                symbols, host="gateway.local", user="operator",
            ))

        self.assertEqual(gateway.ensure_calls, 1)
        self.assertEqual(session.started_config.gateway_tcl_port, 7666)
        self.assertIs(session.remote_session, gateway)

    def test_client_uses_coordinator_binding_without_opening_a_second_tcl_forward(self) -> None:
        binding = SimpleNamespace(tcl_endpoint="127.0.0.1:42001")
        coordinated = []

        class Coordinator:
            def __init__(self, remote, profile_id):
                self.remote, self.profile_id = remote, profile_id
                self.ensure_calls = 0
            def ensure_ready(self):
                self.ensure_calls += 1
                return SimpleNamespace(state="READY", binding=binding, reason_code="")
            def close(self):
                pass

        class ClientSession(_Session):
            def start_client(self, config, remote_session=None):
                self.started_config = config
                self.remote_session = remote_session
                return self.start_local(config)

        remote = SimpleNamespace(gateway_status=lambda: None)
        session = ClientSession(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                _Panel(), remote_session_provider=lambda _request: remote,
                coordinator_factory=lambda source, profile: coordinated.append(Coordinator(source, profile)) or coordinated[-1],
                session_factory=lambda **_kwargs: session, worker_factory=_InlineWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.client(
                symbols, host="gateway.local", user="operator", profile_id="lab",
            ))
        self.assertEqual(coordinated[0].ensure_calls, 1)
        self.assertEqual(session.started_config.bound_tcl_endpoint, "127.0.0.1:42001")
        self.assertIs(session.remote_session, remote)

    def test_session_factory_failure_after_lease_acquisition_releases_gateway_lease(self) -> None:
        """A post-acquire construction failure must not strand a live Gateway lease."""
        class LeaseClient:
            def __init__(self, *_args, **_kwargs):
                self.grant = None
                self.closed = False

            def start(self, _mode, *, probe_serial=None):
                self.grant = SimpleNamespace(public={"tcl_endpoint": "127.0.0.1:42001"})

            def close(self):
                self.closed = True

        leases = []
        remote = SimpleNamespace(
            supports_gateway_leases=True,
            ensure_gateway_agent=lambda: None,
            acquire_gateway=lambda: None,
        )
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                _Panel(), remote_session_provider=lambda _request: remote,
                lease_client_factory=lambda *args, **kwargs: leases.append(LeaseClient(*args, **kwargs)) or leases[-1],
                session_factory=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("session construction failed")),
                worker_factory=_InlineWorker,
            )
            with self.assertRaisesRegex(RuntimeError, "session construction failed"):
                controller.start(live_monitor_controller.LiveMonitorRequest.client(
                    symbols, host="gateway.local", user="operator", profile_id="lab",
                ))

        self.assertEqual(len(leases), 1)
        self.assertTrue(leases[0].closed)
        self.assertFalse(controller.active)
        self.assertIsNone(controller._gateway_lease_client)
        self.assertIsNone(controller._gateway_coordinator)
        self.assertIsNone(controller._gateway_binding)
        self.assertIsNone(controller._live_session)
        self.assertIsNone(controller._worker)

    def test_lease_loss_during_session_factory_rolls_back_before_worker_starts(self) -> None:
        """Heartbeat loss while constructing a session cannot activate its worker."""
        class LeaseClient:
            def __init__(self, _remote, *, on_lost=None, **_kwargs):
                self.grant = None
                self.closed = False
                self._on_lost = on_lost

            def start(self, _mode, *, probe_serial=None):
                self.grant = SimpleNamespace(public={"tcl_endpoint": "127.0.0.1:42001"})

            def lose_heartbeat(self):
                self.grant = None
                self._on_lost()

            def close(self):
                self.closed = True

        leases, workers = [], []
        remote = SimpleNamespace(
            supports_gateway_leases=True,
            ensure_gateway_agent=lambda: None,
            acquire_gateway=lambda: None,
        )
        session = _Session(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                _Panel(), remote_session_provider=lambda _request: remote,
                lease_client_factory=lambda *args, **kwargs: leases.append(LeaseClient(*args, **kwargs)) or leases[-1],
                session_factory=lambda **_kwargs: leases[0].lose_heartbeat() or session,
                worker_factory=lambda *args: workers.append(_InlineWorker(*args)) or workers[-1],
            )
            with self.assertRaisesRegex(RuntimeError, "Gateway lease lost during Monitor startup"):
                controller.start(live_monitor_controller.LiveMonitorRequest.client(
                    symbols, host="gateway.local", user="operator", profile_id="lab",
                ))

        self.assertTrue(leases[0].closed)
        self.assertTrue(session.closed)
        self.assertEqual(workers, [])
        self.assertFalse(controller.active)
        self.assertIsNone(controller._gateway_lease_client)
        self.assertIsNone(controller._gateway_coordinator)
        self.assertIsNone(controller._gateway_binding)
        self.assertIsNone(controller._live_session)
        self.assertIsNone(controller._worker)

    def test_cancelled_client_login_does_not_create_transport(self) -> None:
        sessions = []
        def cancelled(request):
            raise RuntimeError("Client login cancelled")
        controller = LiveMonitorController(
            _Panel(), remote_session_provider=cancelled,
            session_factory=lambda **_kwargs: sessions.append(_Session(())),
            worker_factory=_InlineWorker,
        )
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            controller.start(live_monitor_controller.LiveMonitorRequest.client(
                None, host="gateway.local", user="operator", symbol_roots=(Path.cwd(),)))
        self.assertEqual(sessions, [])
        self.assertFalse(controller.active)

    def test_local_start_streams_samples_closes_transport_and_restores_controls(self) -> None:
        self.assertTrue(
            hasattr(live_monitor_controller, "LiveMonitorRequest"),
            "production Monitor needs an explicit immutable start request",
        )
        request_type = live_monitor_controller.LiveMonitorRequest
        panel = _Panel()
        sample0 = SimpleNamespace(cycle=0)
        sample1 = SimpleNamespace(cycle=1)
        session = _Session((sample0, sample1))
        busy = []

        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef("066EFF535052877067142436"),
                openocd_executable="openocd",
                session_factory=lambda **_kwargs: session,
                worker_factory=_InlineWorker,
            )
            controller.operation_state_changed.connect(busy.append)

            controller.start(request_type.local(symbols))

        self.assertEqual(panel.samples, [sample0, sample1])
        self.assertEqual(panel.reset_count, 1)
        self.assertTrue(session.closed)
        self.assertFalse(controller.active)
        self.assertEqual(panel.control_states[0], (False, True, False))
        self.assertEqual(panel.control_states[-1], (True, False, True))
        self.assertEqual(busy, [True, False])
        self.assertEqual(session.started_config.probe.serial, "066EFF535052877067142436")
        self.assertEqual(session.started_config.watch_specs, ("speed:f32",))
        self.assertEqual(session.started_config.sample_limit, 2)
        self.assertEqual(session.started_config.interval_seconds, 0.5)

    def test_controller_forwards_compiled_typed_watches_to_session_config(self) -> None:
        panel = _Panel()
        typed = LiveWatch(
            "g_machine.position.x", "i16", 0x20000002, 2,
            node_id="fixture:g_machine.position.x",
        )
        panel.watch_specs = lambda: ()
        panel.compiled_watches = lambda: (typed,)
        session = _Session(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel, selected_probe=lambda: ProbeRef("ABC"),
                session_factory=lambda **_kwargs: session, worker_factory=_InlineWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))

        self.assertEqual(session.started_config.watch_specs, ())
        self.assertEqual(session.started_config.compiled_watches, (typed,))

    def test_controller_forwards_selected_project_watch_policies_to_session(self) -> None:
        from b300_core.watch_profiles import WatchPolicy
        panel = _Panel()
        policy = WatchPolicy("speed", display_format="float", unit="rpm", group="Drive")
        panel.watch_policies = lambda: (policy,)
        session = _Session(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel, selected_probe=lambda: ProbeRef("ABC"),
                session_factory=lambda **_kwargs: session, worker_factory=_InlineWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))
        self.assertEqual(session.started_config.watch_policies, (policy,))

    def test_client_request_uses_tcl_only_loopback_transport(self) -> None:
        class ClientSession(_Session):
            def start_client(self, config):
                self.started_config = config
                return SimpleNamespace(
                    role="client", transport="ssh-tcl-local-forwarding",
                    tcl_endpoint="127.0.0.1:16666", initial_target_state="running",
                )

        panel = _Panel()
        session = ClientSession(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef("must-not-be-used"),
                session_factory=lambda **_kwargs: session,
                worker_factory=_InlineWorker,
            )
            self.assertTrue(
                hasattr(live_monitor_controller.LiveMonitorRequest, "client"),
                "production Monitor must retain the existing CLIENT capability",
            )
            request = live_monitor_controller.LiveMonitorRequest.client(
                symbols, host="gateway.local", user="operator", ssh_port=22,
            )
            controller.start(request)

        self.assertEqual(session.started_config.host, "gateway.local")
        self.assertEqual(session.started_config.user, "operator")
        self.assertEqual(session.started_config.ssh_port, 22)
        self.assertEqual(session.started_config.gateway_tcl_port, 6666)
        self.assertEqual(session.started_config.preferred_local_tcl_port, 16666)
        self.assertFalse(session.started_config.show_console)

    def test_finished_worker_is_scheduled_for_qt_cleanup(self) -> None:
        workers = []

        def make_worker(operation, parent=None):
            worker = _InlineWorker(operation, parent)
            workers.append(worker)
            return worker

        panel = _Panel()
        session = _Session(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef(None),
                session_factory=lambda **_kwargs: session,
                worker_factory=make_worker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))

        self.assertEqual(len(workers), 1)
        self.assertTrue(workers[0].deleted)

    def test_stop_cooperatively_cancels_session_and_worker(self) -> None:
        class DeferredWorker(_InlineWorker):
            def start(self) -> None:
                return

            def isRunning(self) -> bool:
                return True

        panel = _Panel()
        panel.mark_stopping = lambda: None
        session = _Session(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.elf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef(None),
                session_factory=lambda **_kwargs: session,
                worker_factory=DeferredWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))
            self.assertTrue(
                hasattr(controller, "stop"),
                "production Monitor needs cooperative stop ownership",
            )
            controller.stop()

        self.assertTrue(session.cancelled)
        self.assertTrue(controller._worker.cancelled)
        self.assertTrue(controller.active)

    def test_start_failure_closes_transport_and_releases_busy_state(self) -> None:
        class FailingSession(_Session):
            def start_local(self, _config):
                raise RuntimeError("probe unavailable")

        class CatchingWorker(_InlineWorker):
            def start(self) -> None:
                try:
                    self.operation(self.log.emit, self.phase.emit, self.cancel_event)
                except Exception as error:
                    self.failed.emit(SimpleNamespace(message=str(error)))
                finally:
                    self.finished.emit()

        panel = _Panel()
        session = FailingSession(())
        busy = []
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef(None),
                session_factory=lambda **_kwargs: session,
                worker_factory=CatchingWorker,
            )
            controller.operation_state_changed.connect(busy.append)
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))

        self.assertTrue(session.closed)
        self.assertEqual(panel.failures, ["probe unavailable"])
        self.assertEqual(panel.control_states[-1], (True, False, False))
        self.assertEqual(busy, [True, False])
        self.assertFalse(controller.active)

    def test_analytics_presentation_failure_does_not_hold_hardware_interlock(self) -> None:
        panel = _Panel()
        panel.apply_analytics = lambda _analytics: (_ for _ in ()).throw(
            ValueError("chart unavailable")
        )
        session = _Session(())
        busy = []
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef(None),
                session_factory=lambda **_kwargs: session,
                worker_factory=_InlineWorker,
            )
            controller.operation_state_changed.connect(busy.append)
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))

        self.assertTrue(session.closed)
        self.assertFalse(controller.active)
        self.assertEqual(busy, [True, False])
        self.assertEqual(len(panel.completed), 1)

    def test_prepare_shutdown_waits_for_worker_and_closes_transport(self) -> None:
        class DeferredWorker(_InlineWorker):
            def start(self) -> None:
                return

            def isRunning(self) -> bool:
                return True

        panel = _Panel()
        panel.mark_stopping = lambda: None
        session = _Session(())
        busy = []
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef(None),
                session_factory=lambda **_kwargs: session,
                worker_factory=DeferredWorker,
            )
            controller.operation_state_changed.connect(busy.append)
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))
            self.assertTrue(
                hasattr(controller, "prepare_shutdown"),
                "window close must wait for Monitor cleanup",
            )
            closed = controller.prepare_shutdown()

        self.assertTrue(closed)
        self.assertTrue(session.cancelled)
        self.assertTrue(session.closed)
        self.assertFalse(controller.active)
        self.assertEqual(busy, [True, False])

    def test_prepare_shutdown_fails_closed_when_worker_does_not_finish(self) -> None:
        class StuckWorker(_InlineWorker):
            def start(self) -> None:
                return

            def isRunning(self) -> bool:
                return True

            def wait(self, milliseconds: int) -> bool:
                self.wait_timeout = milliseconds
                return False

        panel = _Panel()
        panel.mark_stopping = lambda: None
        session = _Session(())
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef(None),
                session_factory=lambda **_kwargs: session,
                worker_factory=StuckWorker,
            )
            controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))

            self.assertFalse(controller.prepare_shutdown())

        self.assertTrue(session.cancelled)
        self.assertFalse(session.closed)
        self.assertTrue(controller.active)
        self.assertEqual(controller._worker.wait_timeout, 3000)

    def test_worker_start_failure_closes_session_and_releases_interlock(self) -> None:
        workers = []

        class StartFailWorker(_InlineWorker):
            def __init__(self, operation, parent=None) -> None:
                super().__init__(operation, parent)
                workers.append(self)

            def start(self) -> None:
                raise OSError("thread start failed")

        panel = _Panel()
        session = _Session(())
        busy = []
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.axf"
            symbols.write_bytes(b"ELF")
            controller = LiveMonitorController(
                panel,
                selected_probe=lambda: ProbeRef(None),
                session_factory=lambda **_kwargs: session,
                worker_factory=StartFailWorker,
            )
            controller.operation_state_changed.connect(busy.append)
            with self.assertRaisesRegex(OSError, "thread start failed"):
                controller.start(live_monitor_controller.LiveMonitorRequest.local(symbols))

        self.assertTrue(session.closed)
        self.assertFalse(controller.active)
        self.assertEqual(busy, [True, False])
        self.assertEqual(panel.control_states[-1], (True, False, False))
        self.assertTrue(workers[0].deleted)

    def test_export_delegates_to_panel_and_reports_saved_path(self) -> None:
        panel = _Panel()
        destination = Path("monitor.csv")
        panel.export_samples = lambda _parent: destination
        controller = LiveMonitorController(panel)
        messages = []
        controller.log.connect(messages.append)
        self.assertTrue(
            hasattr(controller, "export"),
            "production Monitor must retain sample export",
        )

        saved = controller.export(object())

        self.assertEqual(saved, destination)
        self.assertEqual(messages, ["Live sampling exported: monitor.csv"])


class LiveMonitorViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_start_button_runs_selected_elf_through_production_controller(self) -> None:
        from b300_gui.app_context import AppContext
        from b300_core.project_profiles import ProjectProfile
        from b300_core.gateway_profiles import GatewayProfile
        from b300_gui.production_live_panel import ProductionLivePanel
        context = AppContext()
        panel = ProductionLivePanel()
        sample = LiveSample(
            cycle=0,
            scheduled_elapsed_seconds=0.0,
            captured_elapsed_seconds=0.001,
            read_duration_seconds=0.001,
            overrun=False,
            pc=0x08010100,
            source=SourceLocation(0x08010100, "MainLoop", "main.c", 42),
            values=(LiveValue("speed", "f32", 0x20000000, 1.5, "0000C03F"),),
        )
        session = _Session((sample,))
        controller = LiveMonitorController(
            panel,
            selected_probe=lambda: ProbeRef("ABC123"),
            session_factory=lambda **_kwargs: session,
            worker_factory=_InlineWorker,
        )
        view = MonitorView(live_panel=panel, controller=controller, context=context)
        try:
            with tempfile.TemporaryDirectory() as directory:
                symbols = Path(directory) / "robot.axf"
                symbols.write_bytes(b"ELF")
                project = ProjectProfile("robot", "Robot", Path(directory), symbols)
                context.set_profiles((project,), (), default_project_id="robot")
                view.variable_tree_panel.set_catalog(SimpleNamespace(fingerprint="ready", roots=lambda *_args: ()))
                view._typed_revision = (*view._revision(symbols)[:2], "ready")
                panel.start_button.setEnabled(True)
                self.assertIs(view.context, context)
                panel.start_button.click()
            self.assertEqual(session.started_config.symbols, symbols.resolve())
            self.assertEqual(session.started_config.probe.serial, "ABC123")
            self.assertEqual(panel.status.text().split(" · ")[0], "Đã hoàn tất")
            self.assertEqual(len(panel.buffer), 1)
            panel.clear_button.click()
            self.assertEqual(len(panel.buffer), 0)
        finally:
            view.deleteLater()
            self.app.processEvents()

    def test_client_mode_uses_saved_gateway_profile_without_transport_fields(self) -> None:
        class ClientSession(_Session):
            def start_client(self, config):
                self.started_config = config
                return SimpleNamespace(
                    role="client", transport="ssh-tcl-local-forwarding",
                    tcl_endpoint="127.0.0.1:16666", initial_target_state="running",
                )

        from b300_gui.app_context import AppContext
        from b300_core.project_profiles import ProjectProfile
        from b300_core.gateway_profiles import GatewayProfile
        from b300_gui.production_live_panel import ProductionLivePanel
        context = AppContext()
        panel = ProductionLivePanel()
        session = ClientSession(())
        controller = LiveMonitorController(
            panel,
            session_factory=lambda **_kwargs: session,
            worker_factory=_InlineWorker,
        )
        profile = RemoteGatewayProfile("gateway.local", "operator", 2222)
        view = MonitorView(
            live_panel=panel,
            controller=controller,
            context=context,
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                symbols = Path(directory) / "robot.elf"
                symbols.write_bytes(b"ELF")
                project = ProjectProfile("robot", "Robot", Path(directory), symbols)
                gateway = GatewayProfile("gateway", "Robot Gateway", profile)
                context.set_profiles((project,), (gateway,), default_project_id="robot",
                                     default_gateway_id="gateway")
                view.variable_tree_panel.set_catalog(SimpleNamespace(fingerprint="ready", roots=lambda *_args: ()))
                view._typed_revision = (*view._revision(symbols)[:2], "ready")
                panel.start_button.setEnabled(True)
                panel.start_button.click()

            self.assertEqual(session.started_config.host, "gateway.local")
            self.assertEqual(session.started_config.user, "operator")
            self.assertEqual(session.started_config.ssh_port, 2222)
            visible_controls = " ".join(
                child.objectName().lower() for child in view.findChildren(QWidget)
            )
            self.assertNotIn("tcl", visible_controls)
            self.assertNotIn("gdb", visible_controls)
        finally:
            view.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
