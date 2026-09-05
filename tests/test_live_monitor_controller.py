from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_core.live_monitor import LiveSample, LiveValue
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
        authenticated = object()
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
        panel.expressions.setText("speed:f32")
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
        panel.expressions.setText("speed:f32")
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
