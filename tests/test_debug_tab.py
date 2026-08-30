from __future__ import annotations

import os
import time
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.debug_service import DebugState
from b300_core.debug_session import DebugSessionInfo
from b300_core.models import ProbeRef
from b300_core.live_monitor import LiveSample, LiveSummary, LiveValue
from b300_core.offline_symbols import SourceLocation
from b300_gui.debug_tab import DebugTab


class FakeTcl:
    def __init__(self, state: str = "running") -> None:
        self.state = state

    def version(self):
        return "OpenOCD test"

    def wait_target_state(self):
        return self.state

    def target_state(self):
        return self.state

    def resume_target(self):
        self.state = "running"
        return self.state

    def read_words(self, address, count):
        return tuple(0 for _ in range(count))


class FakeDebugService:
    def __init__(self, state: DebugState = DebugState.STOPPED) -> None:
        self.state = state
        self.events = []
        self.tcl = FakeTcl()
        self.executable = "openocd"

    def start(self, config, event_sink=None):
        self.events.append(("start", config))
        self.state = DebugState.READY
        if event_sink:
            event_sink("OpenOCD ready")
        return self.state

    def stop(self):
        self.events.append("stop")
        self.state = DebugState.STOPPED


class FakeLiveMonitorSession:
    def __init__(self, openocd_executable=None):
        self.openocd_executable = openocd_executable
        self.active = False
        self._cancel = threading.Event()
        self.config = None
        self.samples = []

    def start_local(self, config):
        self.active = True
        self._cancel.clear()
        self.config = config
        return SimpleNamespace(role="local", transport="swd-tcl-loopback", tcl_endpoint="127.0.0.1:6666", initial_target_state="running")

    def start_client(self, config):
        self.active = True
        self._cancel.clear()
        self.config = config
        return SimpleNamespace(role="client", transport="ssh-tcl-local-forwarding", tcl_endpoint="127.0.0.1:16666", initial_target_state="running")

    def run(self, on_sample=None):
        watches = tuple(self.config.watch_specs)
        limit = int(self.config.sample_limit or 100)
        interval = min(float(self.config.interval_seconds), 0.05)
        self.samples = []
        for cycle in range(limit):
            if self._cancel.is_set():
                break
            values = []
            for index, spec in enumerate(watches, start=1):
                name, value_type = spec.split(":", 1)
                value = (cycle + 1) * index
                values.append(LiveValue(name, value_type, 0x20000030 + index * 4, value, int(value).to_bytes(4, "little").hex().upper()))
            sample = LiveSample(
                cycle, cycle * float(self.config.interval_seconds),
                cycle * float(self.config.interval_seconds) + 0.01, 0.01, False,
                0x08025FDA, SourceLocation(0x08025FDA, "vApplicationIdleHook", "main.c", 87),
                tuple(values),
            )
            self.samples.append(sample)
            if on_sample is not None:
                on_sample(sample)
            if self._cancel.wait(interval):
                break
        return LiveSummary(len(self.samples), float(self.config.interval_seconds),
                           max(0.0, len(self.samples) * interval), 0, self._cancel.is_set(), "running")

    def analytics_snapshot(self):
        return SimpleNamespace(
            timing=SimpleNamespace(mean_read_duration_seconds=0.01, max_schedule_lag_seconds=0.0),
            functions=(SimpleNamespace(function="vApplicationIdleHook", samples=len(self.samples), share=1.0),),
        )

    def cancel(self):
        self._cancel.set()

    def close(self):
        self.active = False


class FakeGdb:
    running = True


class FakeSession:
    def __init__(self, service: FakeDebugService, *, initial="running", attach_state="halted",
                 fail_start: Exception | None = None) -> None:
        self.service = service
        self.gdb = FakeGdb()
        self.initial = initial
        self.state = attach_state
        self.fail_start = fail_start
        self.active = False
        self.events = []
        self.sample_cycle = 0

    def start(self, config, event_sink=None):
        self.events.append(("start", config))
        if self.fail_start is not None:
            raise self.fail_start
        self.service.state = DebugState.CONNECTED
        self.active = True
        if event_sink:
            event_sink("OpenOCD ready")
        return DebugSessionInfo(
            state="CONNECTED",
            gdb_endpoint="127.0.0.1:%d" % config.gdb_port,
            tcl_endpoint="127.0.0.1:%d" % config.tcl_port,
            symbols=str(config.symbol_file.resolve()) if config.symbol_file else None,
            tcl_version="OpenOCD test",
            initial_target_state=self.initial,
        )

    def start_external(self, *, symbol_file=None, gdb_host="127.0.0.1", gdb_port=3333,
                       tcl_host="127.0.0.1", tcl_port=6666):
        self.events.append(("start-external", symbol_file, gdb_host, gdb_port, tcl_host, tcl_port))
        if self.fail_start is not None:
            raise self.fail_start
        self.active = True
        self.state = self.initial
        return DebugSessionInfo(
            state="CONNECTED",
            gdb_endpoint="%s:%d" % (gdb_host, gdb_port),
            tcl_endpoint="%s:%d" % (tcl_host, tcl_port),
            symbols=str(Path(symbol_file).resolve()) if symbol_file else None,
            tcl_version="OpenOCD forwarded",
            initial_target_state=self.initial,
        )

    def target_poll(self):
        self.events.append(("poll", self.state))
        return self.state

    def halt(self):
        self.events.append("halt")
        self.state = "halted"
        return self.state

    def continue_execution(self):
        self.events.append("continue")
        self.state = "running"
        return self.state

    def reset_halt(self):
        self.events.append("reset-halt")
        self.state = "halted"
        return self.state

    def load_symbols(self, symbol_file):
        path = Path(symbol_file).resolve()
        self.events.append(("load-symbols", path))
        return str(path)

    def step_once(self, timeout_seconds=5.0):
        self.events.append(("step-into", timeout_seconds))
        if self.state != "halted":
            raise RuntimeError("Step Into requires HALTED")
        return self.state

    def next_once(self, timeout_seconds=5.0):
        self.events.append(("step-over", timeout_seconds))
        if self.state != "halted":
            raise RuntimeError("Step Over requires HALTED")
        return self.state

    def capture_where(self):
        from types import SimpleNamespace
        self.events.append("where")
        return SimpleNamespace(address="0x08012345", function="main", file="main.c", fullname=None, line=42)

    def capture_stack(self, limit):
        from types import SimpleNamespace
        self.events.append(("stack", limit))
        return (
            SimpleNamespace(address="0x08012345", function="main", file="main.c", fullname=None, line=42),
            SimpleNamespace(address="0x08012000", function="task", file="task.c", fullname=None, line=7),
        )

    def capture_registers(self):
        from types import SimpleNamespace
        self.events.append("registers")
        return (SimpleNamespace(name="pc", value="0x08012345"), SimpleNamespace(name="sp", value="0x20001000"))

    def capture_variable(self, expression):
        from types import SimpleNamespace
        self.events.append(("variable", expression))
        return SimpleNamespace(expression=expression, value="1")

    def capture_variables(self, expressions):
        from types import SimpleNamespace
        self.sample_cycle += 1
        selected = tuple(expressions)
        self.events.append(("variables", selected, self.sample_cycle))
        return tuple(
            SimpleNamespace(expression=expression, value=str(self.sample_cycle * index))
            for index, expression in enumerate(selected, start=1)
        )

    def read_words(self, address, count):
        self.events.append(("read-words", address, count))
        return tuple(0 for _ in range(count))

    def break_once(self, location, timeout_seconds=5.0):
        from types import SimpleNamespace
        self.events.append(("break-once", location, timeout_seconds))
        self.state = "running"
        return SimpleNamespace(
            kind="hardware-breakpoint", number=1, location=location,
            reason="breakpoint-hit",
            frame=SimpleNamespace(address="0x08025FDA", function=location, file="main.c", fullname=None, line=87),
            value=None,
        )

    def watch_once(self, expression, timeout_seconds=5.0):
        from types import SimpleNamespace
        self.events.append(("watch-once", expression, timeout_seconds))
        self.state = "running"
        return SimpleNamespace(
            kind="watchpoint", number=2, location=expression,
            reason="watchpoint-trigger",
            frame=SimpleNamespace(address="0x0802B62A", function="xTaskIncrementTick", file="tasks.c", fullname=None, line=2813),
            value=SimpleNamespace(expression=expression, value="123"),
        )

    def stop(self):
        self.events.append("stop")
        if self.initial == "running":
            self.state = "running"
        self.active = False
        self.service.state = DebugState.STOPPED


class FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None, **_kwargs):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class FakeTunnel:
    def __init__(self, config, events):
        self.config = config
        self.events = events
        self.active = False

    def start(self):
        self.events.append(("tunnel-start", self.config))
        self.active = True
        return "OpenOCD forwarded"

    def stop(self):
        self.events.append("tunnel-stop")
        self.active = False


class DebugTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for Qt worker completion")

    def make_tab(self, *, initial="running", attach_state="halted", fail_start=None,
                 probe_count=1, settings=None):
        service = FakeDebugService(DebugState.STOPPED)
        session = FakeSession(
            service, initial=initial, attach_state=attach_state, fail_start=fail_start,
        )
        tunnel_events = []
        tab = DebugTab(
            service, lambda: ProbeRef("DEBUG123"), debug_session=session,
            tcl_factory=lambda _endpoint: service.tcl, probe_count=lambda: probe_count,
            tunnel_factory=lambda config: FakeTunnel(config, tunnel_events), settings=settings,
            live_session_factory=FakeLiveMonitorSession,
        )
        tab._test_tunnel_events = tunnel_events
        return tab, service, session

    def test_short_viewport_scrolls_instead_of_compressing_diagnostics(self) -> None:
        tab, _service, _session = self.make_tab()
        tab.resize(1180, 520)
        tab.show()
        self.app.processEvents()
        self.assertGreater(tab.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(tab.diagnostic_view.height(), tab.diagnostic_view.minimumHeight())
        self.assertGreaterEqual(tab.log_view.height(), tab.log_view.minimumHeight())
        self.assertLess(
            tab.diagnostics_box.geometry().bottom(),
            tab.log_box.geometry().top(),
            "Diagnostics and log groups must remain vertically separated.",
        )
        tab.close()

    def test_debug_profile_restore_is_atomic_and_keeps_gateway_identity(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            symbols = root / "firmware.axf"
            symbols.write_bytes(b"AXF")
            settings = FakeSettings({
                "debug/mode": "client",
                "debug/gateway_host": "gateway.local",
                "debug/gateway_user": "automation",
                "debug/gateway_ssh_port": 2222,
                "debug/last_symbols": str(symbols),
                "debug/symbol_root": str(root),
                "debug/sample_expressions": "xTickCount, motorSpeed",
                "debug/sample_cycles": 250,
                "debug/sample_interval": 0.2,
            })
            tab, _service, _session = self.make_tab(probe_count=0, settings=settings)
            self.assertEqual(tab.mode_combo.currentData(), "client")
            self.assertEqual(tab.client_host.text(), "gateway.local")
            self.assertEqual(tab.client_user.text(), "automation")
            self.assertEqual(tab.client_ssh_port.value(), 2222)
            self.assertEqual(tab.symbol_path.text(), str(symbols))
            self.assertEqual(tab._symbol_root, root.resolve())
            self.assertEqual(tab.sample_expressions.text(), "xTickCount, motorSpeed")
            self.assertEqual(tab.sample_cycles.value(), 250)
            self.assertAlmostEqual(tab.sample_interval.value(), 0.2, places=2)
            self.assertEqual(settings.values["debug/gateway_user"], "automation")
            tab.close()

    def test_debug_surface_uses_one_integrated_start_and_state_aware_controls(self) -> None:
        tab, _service, _session = self.make_tab()
        self.assertEqual(tab.gdb_port.text(), "3333")
        self.assertEqual(tab.start_button.text(), "BẮT ĐẦU LOCAL")
        self.assertIn("ELF/AXF", tab.symbol_browse_button.text())
        self.assertFalse(hasattr(tab, "connect_button"))
        self.assertEqual(tab.remote_server_button.text(), "Gateway nhanh")
        self.assertTrue(tab.remote_server_button.isEnabled())
        self.assertEqual(tab.remote_kit_button.text(), "Xuất VS Code Kit…")
        self.assertTrue(tab.remote_kit_button.isHidden())
        self.assertTrue(tab.connection_box.isHidden())
        self.assertFalse(tab.symbols_box.isHidden())
        self.assertFalse(tab.halt_button.isEnabled())
        self.assertFalse(tab.continue_button.isEnabled())
        self.assertFalse(tab.reset_button.isEnabled())
        self.assertFalse(tab.step_into_button.isEnabled())
        self.assertFalse(tab.step_over_button.isEnabled())
        self.assertFalse(tab.break_once_button.isEnabled())
        self.assertFalse(tab.watch_once_button.isEnabled())
        self.assertIn("tự chọn loopback", tab.tcl_display.text())
        tab.close()

    def test_start_loads_symbols_and_auto_resumes_previously_running_target(self) -> None:
        tab, service, session = self.make_tab(initial="running", attach_state="halted")
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.elf"
            symbols.write_bytes(b"ELF")
            tab.symbol_path.setText(str(symbols))
            from types import SimpleNamespace
            from unittest import mock
            matched = SimpleNamespace(
                path=symbols.resolve(), matched=True, matched_samples=4, total_samples=4,
                score=1.0, reason="match",
            )
            with mock.patch(
                "b300_gui.debug_tab.find_matching_symbol_file", return_value=(matched, (matched,)),
            ):
                tab.start_debug()
                self.wait_until(lambda: tab._worker is None)

        self.assertEqual(service.state, DebugState.CONNECTED)
        self.assertTrue(session.active)
        self.assertIn("continue", session.events)
        self.assertEqual(tab._target_state, "running")
        self.assertEqual(tab.interactive_panel.workspace_target_state.text(), "Target: RUNNING")
        self.assertTrue(tab.halt_button.isEnabled())
        self.assertFalse(tab.continue_button.isEnabled())
        self.assertTrue(tab.reset_button.isEnabled())
        self.assertIn("TARGET RUNNING", tab.status_label.text())
        start_config = next(item[1] for item in session.events if isinstance(item, tuple) and item[0] == "start")
        self.assertIsNotNone(start_config.tcl_port)
        self.assertIsNone(start_config.symbol_file)
        self.assertIn(("load-symbols", symbols.resolve()), session.events)
        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        tab.close()

    def test_halt_and_continue_buttons_follow_verified_target_state(self) -> None:
        tab, _service, session = self.make_tab(initial="running", attach_state="halted")
        tab.start_debug()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._target_state, "running")

        tab.halt_target()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._target_state, "halted")
        self.assertFalse(tab.halt_button.isEnabled())
        self.assertTrue(tab.continue_button.isEnabled())
        self.assertTrue(tab.step_into_button.isEnabled())
        self.assertTrue(tab.step_over_button.isEnabled())
        self.assertIn("TARGET HALTED", tab.status_label.text())

        tab.step_into_target()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._target_state, "halted")
        tab.step_over_target()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._target_state, "halted")

        tab.continue_target()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._target_state, "running")
        self.assertEqual(tab.interactive_panel.workspace_target_state.text(), "Target: RUNNING")
        self.assertEqual(tab.interactive_panel.workspace_last_action.text(), "Last action: Continue")
        self.assertTrue(tab.halt_button.isEnabled())
        self.assertFalse(tab.continue_button.isEnabled())
        self.assertIn("halt", session.events)
        self.assertGreaterEqual(session.events.count("continue"), 2)
        self.assertIn(("step-into", 5.0), session.events)
        self.assertIn(("step-over", 5.0), session.events)

        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        tab.close()

    def test_source_level_diagnostics_render_results_without_changing_target_state(self) -> None:
        tab, _service, session = self.make_tab(initial="running", attach_state="halted")
        tab.start_debug()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._target_state, "running")
        self.assertTrue(tab.where_button.isEnabled())

        tab.inspect_where()
        self.wait_until(lambda: tab._worker is None)
        self.assertIn("main", tab.diagnostic_view.toPlainText())
        self.assertIn("main.c:42", tab.diagnostic_view.toPlainText())
        self.assertEqual(tab.interactive_panel.workspace_tabs.currentIndex(), 0)
        self.assertIn("main.c:42", tab.interactive_panel.location_view.toPlainText())
        self.assertEqual(tab.interactive_panel.workspace_target_state.text(), "Target: RUNNING")
        self.assertEqual(tab.interactive_panel.workspace_last_action.text(), "Last action: Where")
        self.assertEqual(tab._target_state, "running")

        tab.variable_expression.setText("bRUN")
        tab.inspect_variable()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab.diagnostic_view.toPlainText(), "bRUN = 1")
        self.assertEqual(tab.interactive_panel.workspace_tabs.currentIndex(), 3)
        self.assertEqual(tab.interactive_panel.variables_view.toPlainText(), "bRUN = 1")
        self.assertEqual(tab.interactive_panel.workspace_last_action.text(), "Last action: Variable")
        self.assertIn(("variable", "bRUN"), session.events)
        self.assertEqual(tab._target_state, "running")

        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        tab.close()

    def test_live_variables_stream_into_table_without_gdb_sampling(self) -> None:
        tab, _service, session = self.make_tab(initial="running", attach_state="halted")
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"fake")
            tab.symbol_path.setText(str(symbols))
            tab._refresh_controls()
            self.assertTrue(tab.sample_start_button.isEnabled())
            self.assertFalse(tab.session.active)
            tab.sample_expressions.setText("xTickCount, motorSpeed")
            tab.sample_cycles.setValue(3)
            tab.sample_interval.setValue(0.1)
            tab.start_live_sampling()
            self.wait_until(lambda: tab._worker is None, timeout=3.0)

        self.assertFalse(tab._sampling_active)
        self.assertIsNone(tab._target_state)
        self.assertEqual(tab.sample_table.rowCount(), 2)
        self.assertEqual(len(tab._sample_buffer), 6)
        self.assertEqual(tab.sample_table.item(0, 0).text(), "xTickCount")
        self.assertEqual(tab.sample_table.item(0, 1).text(), "3")
        self.assertEqual(tab.sample_table.item(1, 1).text(), "6")
        self.assertEqual(
            [item for item in session.events if isinstance(item, tuple) and item[0] == "variables"], []
        )
        self.assertEqual(tab.live_panel.timeline_table.rowCount(), 3)
        self.assertTrue(tab.sample_export_button.isEnabled())
        self.assertTrue(tab.sample_clear_button.isEnabled())
        tab.close()

    def test_live_sampling_stop_is_cooperative_and_export_uses_ring_buffer(self) -> None:
        from unittest import mock

        tab, _service, session = self.make_tab(initial="running", attach_state="halted")
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"fake")
            tab.symbol_path.setText(str(symbols))
            tab.sample_expressions.setText("xTickCount")
            tab.sample_cycles.setValue(100)
            tab.sample_interval.setValue(1.0)
            tab.start_live_sampling()
            self.wait_until(lambda: len(tab._sample_buffer) >= 1, timeout=2.0)
            self.assertTrue(tab.sample_stop_button.isEnabled())
            tab.stop_live_sampling()
            self.wait_until(lambda: tab._worker is None, timeout=2.0)
            self.assertFalse(tab._sampling_active)
            self.assertGreaterEqual(len(tab._sample_buffer), 1)
            self.assertLess(len(tab._sample_buffer), 100)
            output = Path(directory) / "live.csv"
            with mock.patch(
                "b300_gui.debug_live_panel.QFileDialog.getSaveFileName",
                return_value=(str(output), "CSV (*.csv)"),
            ):
                tab.export_live_samples()
            text = output.read_text(encoding="utf-8")
        self.assertIn("xTickCount", text)
        self.assertIn("TIMELINE", text)
        self.assertEqual(
            [item for item in session.events if isinstance(item, tuple) and item[0] == "variables"], []
        )
        tab.clear_live_samples()
        self.assertEqual(len(tab._sample_buffer), 0)
        self.assertEqual(tab.sample_table.rowCount(), 0)
        self.assertFalse(tab.sample_export_button.isEnabled())
        tab.close()

    def test_live_sampling_is_independent_from_interactive_debug_and_requires_symbols(self) -> None:
        tab, _service, _session = self.make_tab(initial="running", attach_state="halted")
        tab.sample_expressions.setText("xTickCount")
        tab.start_live_sampling()
        self.assertFalse(tab._sampling_active)
        self.assertIn("AXF/ELF", tab.status_label.text())

        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"fake")
            tab.symbol_path.setText(str(symbols))
            tab.sample_expressions.setText("")  # timeline-only is valid
            tab.sample_cycles.setValue(1)
            tab.start_live_sampling()
            self.wait_until(lambda: tab._worker is None, timeout=2.0)
            self.assertEqual(tab.live_panel.timeline_table.rowCount(), 1)
            self.assertEqual(tab.sample_table.rowCount(), 0)

            tab.symbol_path.clear()  # fake AXF is only for the fake Live session
            tab.start_debug()
            self.wait_until(lambda: tab._worker is None)
            tab.sample_expressions.setText("xTickCount")
            tab.start_live_sampling()
            self.assertFalse(tab._sampling_active)
            self.assertIn("Interactive Debug", tab.status_label.text())
            tab.stop_debug()
            self.wait_until(lambda: tab._worker is None)
        tab.close()

    def test_break_and_watch_once_are_exposed_and_restore_running_state(self) -> None:
        tab, _service, session = self.make_tab(initial="running", attach_state="halted")
        tab.start_debug()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._target_state, "running")
        self.assertTrue(tab.break_once_button.isEnabled())
        self.assertTrue(tab.watch_once_button.isEnabled())

        tab.stop_timeout.setValue(3)
        tab.break_location.setText("vApplicationIdleHook")
        tab.break_once()
        self.wait_until(lambda: tab._worker is None)
        self.assertIn(("break-once", "vApplicationIdleHook", 3.0), session.events)
        self.assertIn("hardware-breakpoint", tab.diagnostic_view.toPlainText())
        self.assertIn("Resource deleted automatically", tab.diagnostic_view.toPlainText())
        self.assertEqual(tab._target_state, "running")

        tab.watch_expression.setText("xTickCount")
        tab.watch_once()
        self.wait_until(lambda: tab._worker is None)
        self.assertIn(("watch-once", "xTickCount", 3.0), session.events)
        self.assertIn("watchpoint-trigger", tab.diagnostic_view.toPlainText())
        self.assertIn("xTickCount = 123", tab.diagnostic_view.toPlainText())
        self.assertEqual(tab._target_state, "running")

        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        tab.close()

    def test_auto_symbol_match_reuses_active_session_and_sets_verified_path(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        tab, _service, session = self.make_tab(initial="running", attach_state="halted")
        tab.start_debug()
        self.wait_until(lambda: tab._worker is None)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "Objects" / "main.axf"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"ELF")
            result = SimpleNamespace(
                path=candidate, matched=True, matched_samples=4, total_samples=4,
                score=1.0, reason="match",
            )
            with mock.patch(
                "b300_gui.debug_tab.QFileDialog.getExistingDirectory", return_value=str(root),
            ), mock.patch(
                "b300_gui.debug_tab.discover_symbol_files", return_value=(candidate,),
            ), mock.patch(
                "b300_gui.debug_tab.find_matching_symbol_file", return_value=(result, (result,)),
            ):
                tab.auto_match_symbols()
                self.wait_until(lambda: tab._worker is None)

        self.assertEqual(tab.symbol_path.text(), str(candidate))
        self.assertIn("MATCH", tab.diagnostic_view.toPlainText())
        self.assertTrue(any(isinstance(item, tuple) and item[0] == "poll" for item in session.events))
        self.assertTrue(session.active)
        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        tab.close()

    def test_remote_server_starts_without_local_gdb_and_tracks_target_state(self) -> None:
        tab, service, session = self.make_tab()
        tab.gdb_port.setText("4444")
        tab.start_remote_server()
        self.wait_until(lambda: tab._worker is None)

        self.assertEqual(service.state, DebugState.READY)
        self.assertTrue(tab._remote_server_active)
        self.assertFalse(session.active)
        self.assertEqual(tab._target_state, "running")
        self.assertIn("GATEWAY READY", tab.status_label.text())
        self.assertFalse(tab.halt_button.isEnabled())
        self.assertFalse(tab.continue_button.isEnabled())
        self.assertFalse(tab.where_button.isEnabled())
        config = next(item[1] for item in service.events if isinstance(item, tuple) and item[0] == "start")
        self.assertEqual(config.bind_address, "127.0.0.1")
        self.assertEqual(config.gdb_port, 3333)
        self.assertEqual(tab.gdb_port.text(), "3333")
        self.assertEqual(config.tcl_port, 6666)

        service.tcl.state = "halted"
        tab._poll_debug_service()
        self.assertEqual(tab._target_state, "halted")
        self.assertIn("TARGET HALTED", tab.status_label.text())

        service.tcl.state = "running"
        tab._poll_debug_service()
        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(service.state, DebugState.STOPPED)
        self.assertFalse(tab._remote_server_active)
        tab.close()

    def test_gateway_stop_auto_restores_running_target_before_shutdown(self) -> None:
        tab, service, _session = self.make_tab()
        service.tcl.state = "running"
        tab.start_remote_server()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(tab._initial_target_state, "running")

        service.tcl.state = "halted"
        tab._poll_debug_service()
        self.assertEqual(tab._target_state, "halted")
        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)

        self.assertEqual(service.tcl.state, "running")
        self.assertFalse(tab._remote_server_active)
        self.assertEqual(service.state, DebugState.STOPPED)
        tab.close()

    def test_auto_mode_uses_client_when_no_local_probe_and_connects_one_click(self) -> None:
        tab, service, session = self.make_tab(initial="running", probe_count=0)
        self.assertEqual(tab._resolved_role(), "client")
        self.assertEqual(tab.start_button.text(), "KẾT NỐI GATEWAY")
        tab.client_host.setText("gateway.local")
        tab.client_user.setText("automation")
        from types import SimpleNamespace
        from unittest import mock
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"AXF")
            tab.symbol_path.setText(str(symbols))
            matched = SimpleNamespace(
                path=symbols.resolve(), matched=True, matched_samples=4, total_samples=4,
                score=1.0, reason="match",
            )
            with mock.patch(
                "b300_gui.debug_tab.find_matching_symbol_file",
                return_value=(matched, (matched,)),
            ), mock.patch(
                "b300_gui.debug_tab.managed_identity_file", return_value=symbols,
            ), mock.patch(
                "b300_gui.debug_tab.trusted_known_hosts_file", return_value=symbols,
            ):
                tab.start_selected_mode()
                self.wait_until(lambda: tab._worker is None)

        self.assertTrue(session.active)
        self.assertTrue(tab._client_mode_active)
        self.assertIsNotNone(tab._client_tunnel)
        self.assertTrue(tab._client_tunnel.active)
        self.assertEqual(service.state, DebugState.STOPPED)
        self.assertIn("CLIENT CONNECTED", tab.status_label.text())
        external = next(item for item in session.events if isinstance(item, tuple) and item[0] == "start-external")
        self.assertEqual(external[2], "127.0.0.1")
        self.assertEqual(external[4], "127.0.0.1")
        self.assertNotEqual(external[3], external[5])
        self.assertTrue(any(isinstance(item, tuple) and item[0] == "tunnel-start" for item in tab._test_tunnel_events))
        tunnel_start = next(item for item in tab._test_tunnel_events if isinstance(item, tuple) and item[0] == "tunnel-start")
        self.assertEqual(tunnel_start[1].identity_file, symbols)
        self.assertEqual(tunnel_start[1].known_hosts_file, symbols)

        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        self.assertFalse(tab._client_mode_active)
        self.assertIsNone(tab._client_tunnel)
        self.assertIn("tunnel-stop", tab._test_tunnel_events)
        tab.close()

    def test_client_tunnel_loss_cleans_session_and_releases_interlock(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        tab, _service, session = self.make_tab(initial="running", probe_count=0)
        tab.client_host.setText("gateway.local")
        tab.client_user.setText("automation")
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"AXF")
            tab.symbol_path.setText(str(symbols))
            matched = SimpleNamespace(
                path=symbols.resolve(), matched=True, matched_samples=4, total_samples=4,
                score=1.0, reason="match",
            )
            with mock.patch(
                "b300_gui.debug_tab.find_matching_symbol_file", return_value=(matched, (matched,)),
            ):
                tab.start_selected_mode()
                self.wait_until(lambda: tab._worker is None)
        self.assertTrue(tab._client_mode_active)
        self.assertTrue(session.active)
        tunnel = tab._client_tunnel
        self.assertIsNotNone(tunnel)
        tunnel.active = False
        tab._poll_debug_service()
        self.assertFalse(session.active)
        self.assertFalse(tab._client_mode_active)
        self.assertIsNone(tab._client_tunnel)
        self.assertTrue(tab.start_button.isEnabled())
        self.assertFalse(tab.stop_button.isEnabled())
        self.assertIn("Mất SSH tunnel", tab.status_label.text())
        tab.close()

    def test_client_partial_start_failure_stops_tunnel_and_releases_interlock(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        tab, _service, session = self.make_tab(
            initial="running", probe_count=0, fail_start=RuntimeError("GDB attach rejected"),
        )
        tab.client_host.setText("gateway.local")
        tab.client_user.setText("automation")
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"AXF")
            tab.symbol_path.setText(str(symbols))
            matched = SimpleNamespace(
                path=symbols.resolve(), matched=True, matched_samples=4, total_samples=4,
                score=1.0, reason="match",
            )
            with mock.patch(
                "b300_gui.debug_tab.find_matching_symbol_file", return_value=(matched, (matched,)),
            ):
                tab.start_selected_mode()
                self.wait_until(lambda: tab._worker is None)
        self.assertFalse(session.active)
        self.assertFalse(tab._client_mode_active)
        self.assertIsNone(tab._client_tunnel)
        self.assertIn("tunnel-stop", tab._test_tunnel_events)
        self.assertTrue(tab.start_button.isEnabled())
        self.assertIn("GDB attach rejected", tab.status_label.text())
        tab.close()

    def test_client_symbol_root_is_saved_for_auto_match_without_touching_hardware(self) -> None:
        from unittest import mock
        tab, service, session = self.make_tab(probe_count=0)
        self.assertEqual(tab._resolved_role(), "client")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "Objects" / "firmware.axf"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"AXF")
            with mock.patch(
                "b300_gui.debug_tab.QFileDialog.getExistingDirectory", return_value=str(root),
            ), mock.patch(
                "b300_gui.debug_tab.discover_symbol_files", return_value=(candidate,),
            ):
                tab.auto_match_symbols()

        self.assertEqual(tab._symbol_root, root.resolve())
        self.assertIn("KẾT NỐI GATEWAY", tab.start_button.text())
        self.assertIn("KẾT NỐI GATEWAY", tab.diagnostic_view.toPlainText())
        self.assertEqual(service.state, DebugState.STOPPED)
        self.assertFalse(session.active)
        tab.close()

    def test_auto_mode_prefers_local_when_stlink_is_present(self) -> None:
        tab, _service, session = self.make_tab(initial="running", attach_state="halted", probe_count=1)
        self.assertEqual(tab._resolved_role(), "local")
        tab.start_selected_mode()
        self.wait_until(lambda: tab._worker is None)
        self.assertTrue(session.active)
        self.assertTrue(any(isinstance(item, tuple) and item[0] == "start" for item in session.events))
        self.assertFalse(any(isinstance(item, tuple) and item[0] == "start-external" for item in session.events))
        tab.stop_debug()
        self.wait_until(lambda: tab._worker is None)
        tab.close()

    def test_local_start_auto_matches_saved_project_and_loads_verified_symbols(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        with TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "Objects" / "F407" / "main.axf"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"ELF")
            settings = FakeSettings({"debug/symbol_root": str(root), "debug/mode": "local"})
            tab, _service, session = self.make_tab(settings=settings)
            matched = SimpleNamespace(
                path=candidate.resolve(), matched=True, matched_samples=4, total_samples=4,
                score=1.0, reason="match",
            )
            with mock.patch(
                "b300_gui.debug_tab.discover_symbol_files", return_value=(candidate,),
            ), mock.patch(
                "b300_gui.debug_tab.find_matching_symbol_file", return_value=(matched, (matched,)),
            ):
                tab.start_selected_mode()
                self.wait_until(lambda: tab._worker is None)
            self.assertTrue(session.active)
            self.assertEqual(tab.symbol_path.text(), str(candidate.resolve()))
            self.assertIn(("load-symbols", candidate.resolve()), session.events)
            tab.stop_debug()
            self.wait_until(lambda: tab._worker is None)
            tab.close()
            self.app.processEvents()

    def test_local_explicit_symbol_mismatch_fails_closed_and_stops_session(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        tab, service, session = self.make_tab()
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "wrong.axf"
            symbols.write_bytes(b"ELF")
            tab.symbol_path.setText(str(symbols))
            miss = SimpleNamespace(
                path=symbols.resolve(), matched=False, matched_samples=0, total_samples=4,
                score=0.0, reason="machine code mismatch",
            )
            with mock.patch(
                "b300_gui.debug_tab.find_matching_symbol_file", return_value=(None, (miss,)),
            ):
                tab.start_debug()
                self.wait_until(lambda: tab._worker is None)
        self.assertFalse(session.active)
        self.assertEqual(service.state, DebugState.STOPPED)
        self.assertIn("không khớp firmware", tab.status_label.text())
        tab.close()
        self.app.processEvents()

    def test_start_failure_releases_interlock_and_reports_error(self) -> None:
        tab, service, _session = self.make_tab(fail_start=RuntimeError("remote rejected"))
        tab.start_debug()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(service.state, DebugState.STOPPED)
        self.assertTrue(tab.start_button.isEnabled())
        self.assertFalse(tab.stop_button.isEnabled())
        self.assertIn("remote rejected", tab.status_label.text())
        tab.close()

    def test_openocd_failure_watchdog_releases_gui_interlock(self) -> None:
        tab, service, session = self.make_tab(initial="running", attach_state="halted")
        tab.start_debug()
        self.wait_until(lambda: tab._worker is None)
        self.assertTrue(session.active)
        service.state = DebugState.FAILED
        tab._poll_debug_service()
        self.assertFalse(tab._watchdog.isActive())
        self.assertIn("stop", session.events)
        self.assertFalse(session.active)
        self.assertTrue(tab.start_button.isEnabled())
        self.assertFalse(tab.stop_button.isEnabled())
        tab.close()

    def test_external_interlock_blocks_start_but_not_offline_symbol_selection(self) -> None:
        tab, _service, _session = self.make_tab()
        self.assertTrue(tab.start_button.isEnabled())
        tab.set_external_blocked(True)
        self.assertFalse(tab.start_button.isEnabled())
        self.assertFalse(tab.remote_server_button.isEnabled())
        self.assertTrue(tab.remote_kit_button.isEnabled())
        self.assertTrue(tab.symbol_path.isEnabled())
        self.assertTrue(tab.symbol_browse_button.isEnabled())
        tab.set_external_blocked(False)
        self.assertTrue(tab.start_button.isEnabled())
        tab.close()


if __name__ == "__main__":
    unittest.main()
