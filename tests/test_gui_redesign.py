"""Comprehensive GUI & UX redesign test suite for B300 ST-Link Tools."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QHeaderView, QLabel

from b300_core.debug_service import DebugState
from b300_core.debug_sampling import VariableSample
from b300_core.live_monitor import LiveSample, LiveValue
from b300_core.offline_symbols import SourceLocation
from b300_core.debug_session import DebugSessionInfo
from b300_core.metadata import decode_ota_metadata
from b300_core.models import ProbeRef
from b300_gui.collapsible_card import CollapsibleCard
from b300_gui.debug_connection_panel import DebugConnectionPanel
from b300_gui.debug_interactive_panel import DebugInteractivePanel
from b300_gui.debug_live_panel import DebugLivePanel
from b300_gui.debug_log_panel import DebugLogPanel
from b300_gui.debug_plot_panel import DebugPlotPanel
from b300_gui.debug_tab import DebugTab
from b300_gui.main_window import MainWindow
from b300_gui.memory_tab import MemoryTab
from tests.test_core_probe_memory_metadata import make_metadata
from tests.test_debug_tab import FakeDebugService, FakeSession, FakeSettings, FakeTunnel, FakeLiveMonitorSession


def make_sample(cycle: int, elapsed: float, expr: str, raw: str, num) -> VariableSample:
    return VariableSample(
        cycle=cycle, elapsed_seconds=elapsed, captured_at_unix_ms=1000 + cycle,
        expression=expr, raw_value=raw, numeric_value=num,
    )


class GuiRedesignTests(unittest.TestCase):
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
        self.fail("Timed out waiting for condition")

    def make_debug_tab(self, *, initial="running", attach_state="halted", probe_count=1, settings=None):
        service = FakeDebugService(DebugState.STOPPED)
        session = FakeSession(service, initial=initial, attach_state=attach_state)
        tunnel_events = []
        tab = DebugTab(
            service, lambda: ProbeRef("TEST_PROBE"), debug_session=session,
            tcl_factory=lambda _endpoint: service.tcl, probe_count=lambda: probe_count,
            tunnel_factory=lambda config: FakeTunnel(config, tunnel_events), settings=settings,
            live_session_factory=FakeLiveMonitorSession,
        )
        tab._test_tunnel_events = tunnel_events
        return tab, service, session

    def test_collapsible_card_expand_collapse(self) -> None:
        card = CollapsibleCard("Test Section", "Subtitle text", expanded=True)
        card.show()
        self.assertTrue(card.is_expanded())
        self.assertFalse(card.content_widget.isHidden())
        self.assertEqual(card.toggle_btn.text(), "▼")

        card.toggle()
        self.assertFalse(card.is_expanded())
        self.assertTrue(card.content_widget.isHidden())
        self.assertEqual(card.toggle_btn.text(), "▶")

        card.set_expanded(True)
        self.assertTrue(card.is_expanded())
        self.assertFalse(card.content_widget.isHidden())
        card.close()

    def test_debug_layout_on_laptop_1366x768(self) -> None:
        tab, _service, _session = self.make_debug_tab()
        tab.resize(1366, 768)
        tab.show()
        self.app.processEvents()

        self.assertIsNotNone(tab.scroll_area)
        self.assertTrue(tab.scroll_area.widgetResizable())
        self.assertIsNotNone(tab.scroll_content)
        self.assertFalse(tab.conn_panel.isHidden())
        self.assertFalse(tab.live_panel.isHidden())
        self.assertFalse(tab.interactive_panel.isHidden())
        self.assertFalse(tab.log_panel.isHidden())
        tab.close()

    def test_live_panel_adapts_typed_zero_halt_sample_and_watch_specs(self) -> None:
        panel = DebugLivePanel()
        panel.expressions.setText("xTickCount")
        panel.type_combo.setCurrentText("u32")
        self.assertEqual(panel.watch_specs(), ("xTickCount:u32",))
        sample = LiveSample(
            0, 0.0, 0.01, 0.01, False, 0x08025FDA,
            SourceLocation(0x08025FDA, "vApplicationIdleHook", "main.c", 87),
            (
                LiveValue("xTickCount", "u32", 0x20000030, 123, "7B000000"),
                LiveValue("v_current", "f64", 0x20000648, None, "0102030405060708", coherent=False),
            ),
        )
        converted = panel.append_live_sample(sample)
        self.assertEqual(panel.timeline_table.rowCount(), 1)
        self.assertEqual(panel.timeline_table.item(0, 2).text(), "vApplicationIdleHook")
        self.assertEqual(panel.table.rowCount(), 2)
        self.assertEqual(panel.table.item(0, 2).text(), "u32")
        self.assertEqual(panel.table.item(0, 3).text(), "0x20000030")
        self.assertEqual(panel.table.item(1, 1).text(), "<incoherent>")
        self.assertEqual(converted[0].numeric_value, 123.0)
        self.assertIsNone(converted[1].numeric_value)
        panel.close()

    def test_live_monitor_timeline_update_and_follow_latest(self) -> None:
        panel = DebugLivePanel()
        panel.resize(800, 400)
        panel.show()
        self.app.processEvents()

        self.assertTrue(panel.follow_latest_check.isChecked())
        panel.append_timeline_sample(0.125, 0x08024958, "prvIdleTask", "tasks.c", 3463)
        panel.append_timeline_sample(0.219, 0x0802B5A4, "xTaskGetSchedulerState", "tasks.c", 4032)
        panel.append_timeline_sample(0.328, 0x08025FDA, "vApplicationIdleHook", "main.c", 87)

        self.assertEqual(panel.timeline_table.rowCount(), 3)
        self.assertEqual(panel.timeline_table.item(0, 0).text(), "0.125s")
        self.assertEqual(panel.timeline_table.item(0, 1).text(), "0x08024958")
        self.assertEqual(panel.timeline_table.item(0, 2).text(), "prvIdleTask")
        self.assertEqual(panel.timeline_table.item(1, 2).text(), "xTaskGetSchedulerState")
        self.assertEqual(panel.timeline_table.item(2, 2).text(), "vApplicationIdleHook")

        panel.clear_history()
        self.assertEqual(panel.timeline_table.rowCount(), 0)
        panel.close()

    def test_live_monitor_timeline_avoids_resize_to_contents_hot_path(self) -> None:
        panel = DebugLivePanel()
        header = panel.timeline_table.horizontalHeader()
        self.assertEqual(header.sectionResizeMode(0), QHeaderView.ResizeMode.Fixed)
        self.assertEqual(header.sectionResizeMode(1), QHeaderView.ResizeMode.Fixed)
        self.assertEqual(header.sectionResizeMode(2), QHeaderView.ResizeMode.Stretch)
        self.assertEqual(header.sectionResizeMode(3), QHeaderView.ResizeMode.Interactive)
        self.assertEqual(header.sectionResizeMode(4), QHeaderView.ResizeMode.Fixed)
        panel.close()

    def test_live_monitor_timeline_is_bounded_for_long_running_sessions(self) -> None:
        panel = DebugLivePanel()
        panel.TIMELINE_CAPACITY = 3
        for index in range(5):
            panel.append_timeline_sample(
                index * 0.1, 0x08010000 + index * 2,
                "fn%d" % index, "main.c", 10 + index,
            )

        self.assertEqual(panel.timeline_table.rowCount(), 3)
        self.assertEqual(len(panel._timeline_samples), 3)
        self.assertEqual(panel.timeline_table.item(0, 2).text(), "fn2")
        self.assertEqual(panel.timeline_table.item(2, 2).text(), "fn4")
        self.assertEqual(panel._timeline_samples[0]["function"], "fn2")
        panel.close()

    def test_live_variables_table_watch_operations(self) -> None:
        panel = DebugLivePanel()
        panel.expressions.setText("xTickCount")
        panel.type_combo.setCurrentText("u32")
        panel.add_watch_btn.click()

        self.assertEqual(panel.table.rowCount(), 1)
        self.assertEqual(panel.table.item(0, 0).text(), "xTickCount")
        self.assertEqual(panel.table.item(0, 2).text(), "u32")
        self.assertEqual(panel.table.item(0, 5).checkState(), Qt.CheckState.Checked)

        panel.append_batch([make_sample(0, 0.1, "xTickCount", "42", 42)])
        self.assertEqual(panel.table.item(0, 1).text(), "42")

        panel.table.selectRow(0)
        panel.remove_watch_btn.click()
        self.assertEqual(panel.table.rowCount(), 0)
        panel.close()

    def test_live_plot_panel_pause_clear_export(self) -> None:
        plot_panel = DebugPlotPanel(max_points=100)
        samples = [
            make_sample(0, 0.0, "speed", "10", 10.0),
            make_sample(1, 0.1, "speed", "20", 20.0),
        ]
        plot_panel.set_samples(samples)
        self.assertIn("2 points", plot_panel.points_label.text())

        # Test pause display
        plot_panel.pause_btn.setChecked(True)
        self.assertTrue(plot_panel._paused)
        plot_panel.set_samples(samples + [make_sample(2, 0.2, "speed", "30", 30.0)])
        # Plot remains paused
        plot_panel.pause_btn.setChecked(False)
        self.assertFalse(plot_panel._paused)

        # Test clear
        plot_panel.clear()
        self.assertEqual(plot_panel.points_label.text(), "0 points plotted")
        plot_panel.close()

    def test_interactive_debug_warning_visible_and_controls_work(self) -> None:
        panel = DebugInteractivePanel()
        self.assertTrue(panel.is_expanded())
        warn = panel.findChild(QLabel, "interactiveDebugWarningText")
        self.assertIsNotNone(warn)
        self.assertIn("Interactive Debug", warn.text())
        self.assertIn("May halt the MCU", warn.text())

        self.assertIsNotNone(panel.halt_button)
        self.assertIsNotNone(panel.continue_button)
        self.assertIsNotNone(panel.reset_button)
        self.assertIsNotNone(panel.step_into_button)
        self.assertIsNotNone(panel.step_over_button)
        self.assertIsNotNone(panel.where_button)
        self.assertIsNotNone(panel.stack_button)
        self.assertIsNotNone(panel.registers_button)
        self.assertIsNotNone(panel.variable_button)
        self.assertIsNotNone(panel.break_once_button)
        self.assertIsNotNone(panel.watch_once_button)
        panel.close()

    def test_technical_log_panel_badges_and_actions(self) -> None:
        log_panel = DebugLogPanel()
        self.assertFalse(log_panel.is_expanded())  # default collapsed
        self.assertEqual(log_panel.info_badge.text(), "0 INFO")
        self.assertEqual(log_panel.warn_badge.text(), "0 WARN")
        self.assertEqual(log_panel.error_badge.text(), "0 ERR")

        log_panel.append_log("Info: OpenOCD started normally")
        log_panel.append_log("Warn: Connection retry attempt 1")
        log_panel.append_log("Error: Target halted unexpectedly")

        self.assertEqual(log_panel.info_badge.text(), "1 INFO")
        self.assertEqual(log_panel.warn_badge.text(), "1 WARN")
        self.assertEqual(log_panel.error_badge.text(), "1 ERR")

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "debug.log"
            with mock.patch("b300_gui.debug_log_panel.QFileDialog.getSaveFileName", return_value=(str(dest), "Log files (*.log)")):
                saved = log_panel.save_log()
                self.assertEqual(saved, dest)
                self.assertTrue(dest.is_file())
                self.assertIn("OpenOCD started", dest.read_text(encoding="utf-8"))

        log_panel.clear_log()
        self.assertEqual(log_panel.info_badge.text(), "0 INFO")
        self.assertEqual(log_panel.log_view.toPlainText(), "")
        log_panel.close()

    def test_mode_selection_visibility(self) -> None:
        tab, _service, _session = self.make_debug_tab()

        # Local mode
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("local"))
        self.assertFalse(tab.symbols_box.isHidden())
        self.assertTrue(tab.client_box.isHidden())
        self.assertTrue(tab.connection_box.isHidden())
        self.assertIn("LOCAL", tab.start_button.text())

        # Client mode
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("client"))
        self.assertFalse(tab.symbols_box.isHidden())
        self.assertFalse(tab.client_box.isHidden())
        self.assertTrue(tab.connection_box.isHidden())
        self.assertIn("GATEWAY", tab.start_button.text())

        # Gateway mode
        tab.mode_combo.setCurrentIndex(tab.mode_combo.findData("gateway"))
        self.assertTrue(tab.symbols_box.isHidden())
        self.assertTrue(tab.client_box.isHidden())
        self.assertFalse(tab.connection_box.isHidden())
        self.assertIn("GATEWAY", tab.start_button.text())

        tab.close()


    def test_clean_shutdown_while_live_monitor_running(self) -> None:
        tab, _service, session = self.make_debug_tab(initial="running", attach_state="halted")
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"fake")
            tab.symbol_path.setText(str(symbols))
            tab.sample_expressions.setText("xTickCount")
            tab.sample_cycles.setValue(100)
            tab.sample_interval.setValue(1.0)
            tab.start_live_sampling()
            self.wait_until(lambda: tab._worker is not None, timeout=2.0)

            # Prepare shutdown while non-halting worker is running.
            self.assertTrue(tab.prepare_shutdown())
        self.assertIsNone(tab._worker)
        self.assertIsNone(tab._live_session)
        self.assertFalse(tab._sampling_active)
        self.assertFalse(tab._watchdog.isActive())
        self.assertFalse(session.active)
        tab.close()

    def test_memory_application_health_rendering(self) -> None:
        tab = MemoryTab(service=object(), probe_provider=lambda: None)
        metadata = decode_ota_metadata(make_metadata(state=3))
        health = SimpleNamespace(
            metadata=metadata, lifecycle="BOOTABLE", bootable=True,
            reason="Application Metadata, image CRC, and vector permit bootability.",
            next_action="No action is required.", bytes_checked=126580,
            image_crc_valid=True, actual_image_crc32=metadata.image_crc32,
            application_vector=SimpleNamespace(
                valid=True, reset_vector=0x08010361, reason="Application vector is valid."
            ),
        )
        tab.show_application_health(health)
        self.assertEqual(tab.health_values["Lifecycle"].text(), "BOOTABLE")
        self.assertEqual(tab.health_values["Bootable"].text(), "YES")
        self.assertEqual(tab.health_values["Image CRC"].text(), "MATCH")
        self.assertEqual(tab.health_values["Expected CRC32"].text(), "0x%08X" % metadata.image_crc32)
        self.assertEqual(tab.health_values["Actual CRC32"].text(), "0x%08X" % metadata.image_crc32)
        self.assertIn("0x08010361", tab.health_values["Vector"].text())
        self.assertEqual(tab.health_values["Next action"].text(), "No action is required.")
        tab.close()


if __name__ == "__main__":
    unittest.main()
