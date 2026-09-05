"""Production Monitor presentation and shared-context contracts; no hardware."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QPushButton
from b300_core.live_monitor import LiveSample, LiveValue
from b300_core.offline_symbols import SourceLocation
from b300_core.remote_profile import RemoteGatewayProfile
from b300_gui.production_live_panel import ProductionLivePanel
from b300_gui.views.monitor_view import MonitorView

class Context(QObject):
    changed = Signal()
    selected_project = None
    selected_connection = None

def sample(cycle=0, value=12.5, coherent=True):
    return LiveSample(cycle, cycle * .5, cycle * .5, .002, False, 0x08010000,
                      SourceLocation(0x08010000, "main", "main.c", 1),
                      (LiveValue("speed", "f32", 0x20000020, value, "00000000", coherent),))

class EngineeringMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = ProductionLivePanel()
        self.addCleanup(panel.deleteLater)
        return panel

    def test_small_view_does_not_overlap_table_and_trend(self):
        view = MonitorView(context=Context())
        self.addCleanup(view.deleteLater)
        view.resize(1140, 550)
        view.show()
        self.app.processEvents()
        panel = view.live_panel
        self.assertLess(panel.table.geometry().bottom(), panel.detail_splitter.geometry().top())

    def test_samples_render_actual_values_and_filter_without_changing_watch_list(self):
        panel = self.panel()
        panel.append_live_sample(sample())
        self.assertEqual(panel.table.item(0, 1).text(), "12.5")
        self.assertEqual(panel.table.item(0, 3).text(), "0x20000020")
        self.assertEqual(panel.table.item(0, 9).text(), "Nhất quán")
        panel.search_filter.setText("missing")
        self.assertTrue(panel.table.isRowHidden(0))
        self.assertEqual(panel.watch_specs(), ("speed:f32",))
        panel.search_filter.setText("SPEED")
        self.assertFalse(panel.table.isRowHidden(0))
        panel.append_live_sample(sample(1, 99, False))
        self.assertEqual(panel.table.item(0, 9).text(), "Không nhất quán")
        self.assertEqual(panel.table.item(0, 1).text(), "<không nhất quán>")
        self.assertEqual(panel.buffer.snapshot()[-1].raw_value, "<incoherent>")
        self.assertNotIn(99, [point[1] for point in panel.trend.points("speed")])

    def test_trend_and_recent_samples_are_bounded_and_clear_with_history(self):
        panel = self.panel()
        self.assertEqual(panel.trend.points("speed"), ())
        for i in range(450):
            panel.append_live_sample(sample(i, i))
        self.assertLessEqual(len(panel.trend.points("speed")), 240)
        self.assertEqual(panel.trend.points("speed")[-1], (224.5, 449.0))
        self.assertLessEqual(panel.recent_table.rowCount(), 200)
        panel.clear_history()
        self.assertEqual(panel.trend.points("speed"), ())
        self.assertEqual(panel.recent_table.rowCount(), 0)

    def test_refresh_presets_and_watch_presets_keep_existing_behavior(self):
        panel = self.panel()
        self.assertEqual([panel.interval_preset_combo.itemData(i) for i in range(6)],
                         [.1, .2, .5, 1., 2., 5.])
        panel.interval_preset_combo.setCurrentIndex(4)
        self.assertEqual(panel.interval.value(), 2.)
        panel.expressions.setText("speed")
        panel.type_combo.setCurrentText("f32")
        panel.add_watch_btn.click()
        with tempfile.TemporaryDirectory() as directory:
            path = panel.export_preset(Path(directory) / "watches.json")
            panel.clear_history()
            panel.import_preset(path)
        self.assertEqual(panel.watch_specs(), ("speed:f32",))
        panel.table.selectRow(0)
        panel.remove_watch_btn.click()
        self.assertEqual(panel.table.rowCount(), 0)

    def test_session_summary_uses_only_received_sample_evidence(self):
        view = MonitorView(context=Context())
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.last_sample.text(), "Mẫu gần nhất: —")
        self.assertEqual(view.sample_health.text(), "Chất lượng mẫu: chưa kiểm tra")
        view.append_live_sample(sample(3, 1.25, False))
        self.assertEqual(view.last_sample.text(), "Mẫu gần nhất: 1.500 s")
        self.assertEqual(view.sample_health.text(), "Chất lượng mẫu: giá trị không nhất quán")
        view.reset_for_sampling()
        self.assertEqual(view.last_sample.text(), "Mẫu gần nhất: —")

    def test_stop_button_cancels_controller_owned_session(self):
        from tests.test_live_monitor_controller import _InlineWorker, _Session
        from b300_core.models import ProbeRef
        class WaitingWorker(_InlineWorker):
            def start(self):
                pass
        context = Context()
        context.selected_connection = SimpleNamespace(is_local=True, gateway=None)
        view = MonitorView(context=context, selected_probe=lambda: ProbeRef("fixture"))
        self.addCleanup(view.deleteLater)
        session = _Session(())
        view.controller._session_factory = lambda **kwargs: session
        view.controller._worker_factory = WaitingWorker
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"ELF")
            context.selected_project = SimpleNamespace(symbols=symbols, workspace=Path(directory))
            view.live_panel.start_button.click()
            self.assertTrue(view.controller.active)
            view.live_panel.stop_button.click()
            self.assertTrue(session.cancelled)
            self.assertTrue(view.controller._worker.cancel_event.is_set())
            self.assertTrue(view.controller.prepare_shutdown())

    def test_narrow_page_does_not_force_wide_context_sidebar(self):
        view = MonitorView(context=Context())
        self.addCleanup(view.deleteLater)
        view.resize(900, 760)
        view.show()
        self.app.processEvents()
        self.assertLessEqual(view.width(), 900)
        self.assertFalse(view.session_card.isVisible())
        view.resize(1900, 1000)
        self.app.processEvents()
        self.assertTrue(view.session_card.isVisible())
        view.close()

    def test_context_chooses_local_or_gateway_request_without_page_selectors(self):
        context = Context()
        view = MonitorView(context=context)
        self.addCleanup(view.deleteLater)
        self.assertFalse(any("VS Code" in b.text() for b in view.findChildren(QPushButton)))
        for attribute in ("role_selector", "project_selector", "gateway_selector", "symbol_button"):
            self.assertFalse(hasattr(view, attribute), attribute)
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"ELF")
            context.selected_project = SimpleNamespace(symbols=symbols, workspace=Path(directory))
            context.selected_connection = SimpleNamespace(is_local=True, gateway=None)
            context.changed.emit()
            with mock.patch.object(view.controller, "start") as start:
                view.live_panel.start_button.click()
                self.assertEqual(start.call_args.args[0].role, "LOCAL")
                self.assertEqual(start.call_args.args[0].symbols, symbols)
                context.selected_connection = SimpleNamespace(is_local=False, gateway=SimpleNamespace(
                    endpoint=RemoteGatewayProfile("gateway.local", "operator", 2222)))
                context.changed.emit()
                view.live_panel.start_button.click()
                request = start.call_args.args[0]
                self.assertEqual((request.role, request.host, request.user, request.ssh_port),
                                 ("CLIENT", "gateway.local", "operator", 2222))
            view.live_panel.stop_button.setEnabled(True)
            view.set_hardware_busy(True)
            self.assertTrue(view.live_panel.stop_button.isEnabled())
            self.assertFalse(view.live_panel.start_button.isEnabled())

if __name__ == "__main__": unittest.main()
