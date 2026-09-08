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
from b300_core.live_monitor import LiveSample, LiveValue, LiveWatch
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
        self.assertEqual(panel.watch_specs(), ())
        panel.search_filter.setText("SPEED")
        self.assertFalse(panel.table.isRowHidden(0))
        panel.append_live_sample(sample(1, 99, False))
        self.assertEqual(panel.table.item(0, 9).text(), "Không nhất quán")
        self.assertEqual(panel.table.item(0, 1).text(), "<không nhất quán>")
        self.assertEqual(panel.buffer.snapshot()[-1].raw_value, "<incoherent>")
        self.assertNotIn(99, [point[1] for point in panel.trend.points("speed")])

    def test_policy_group_editor_formats_selected_watch_without_manual_json(self):
        from b300_core.live_monitor import LiveWatch
        from b300_core.watch_profiles import WatchPolicy
        panel = self.panel()
        panel.add_compiled_watch(LiveWatch("speed", "u32", 0x20000020, 4))
        panel.table.setCurrentCell(panel.rows["speed"], 0)
        panel.set_watch_policies((WatchPolicy("speed", group="Drive", unit="rpm", scale=.5),))
        self.assertEqual(panel.policy_group.currentText(), "Drive")
        panel.append_live_sample(sample(value=20))
        self.assertEqual(panel.table.item(panel.rows["speed"], 1).text(), "10 rpm")
        panel.policy_group.setCurrentText("General")
        panel.policy_format.setCurrentText("hex")
        panel.save_selected_policy()
        self.assertEqual(panel.watch_policies()[0].group, "General")
        self.assertEqual(panel.watch_policies()[0].display_format, "hex")

    def test_policy_editor_persists_project_sidecar(self):
        from b300_core.live_monitor import LiveWatch
        from b300_core.watch_profiles import load_watch_policies
        context = Context()
        with tempfile.TemporaryDirectory() as directory:
            context.selected_project = SimpleNamespace(symbols=None, workspace=Path(directory))
            view = MonitorView(context=context)
            self.addCleanup(view.deleteLater)
            panel = view.live_panel
            panel.add_compiled_watch(LiveWatch("speed", "u32", 0x20000020, 4))
            panel.table.setCurrentCell(panel.rows["speed"], 0)
            panel.policy_group.setCurrentText("Drive")
            panel.policy_unit.setText("rpm")
            panel.save_selected_policy()
            saved = load_watch_policies(Path(directory) / ".b300-watch-policies.json")
            self.assertEqual(saved[0].group, "Drive")
            self.assertEqual(saved[0].unit, "rpm")

    def test_analytics_renders_delta_rate_and_policy_threshold_state(self):
        from b300_core.live_analytics import LiveMonitorStore
        from b300_core.live_monitor import LiveWatch
        from b300_core.watch_profiles import WatchPolicy
        panel = self.panel()
        panel.add_compiled_watch(LiveWatch("speed", "f32", 0x20000020, 4))
        policy = WatchPolicy("speed", maximum=10)
        panel.set_watch_policies((policy,))
        store = LiveMonitorStore(watch_policies=(policy,))
        store.append(sample(cycle=0, value=5))
        store.append(sample(cycle=1, value=15))
        panel.apply_analytics(store.snapshot())
        row = panel.rows["speed"]
        self.assertEqual(panel.table.item(row, 12).text(), "10")
        self.assertEqual(panel.table.item(row, 13).text(), "20")
        self.assertEqual(panel.table.item(row, 9).text(), "Vượt ngưỡng")

    def test_partial_batch_keeps_other_rows_and_displays_batch_progress(self):
        panel = self.panel()
        panel.add_compiled_watch(LiveWatch(
            "speed", "f32", 0x20000020, 4, node_id="typed:speed"
        ))
        panel.add_compiled_watch(LiveWatch(
            "direction", "u8", 0x20000024, 1, node_id="typed:direction"
        ))
        first = LiveSample(
            0, 0.0, 0.0, .002, False, 0x08010000,
            SourceLocation(0x08010000, "main", "main.c", 1),
            (LiveValue("direction", "u8", 0x20000024, 7, "07", node_id="typed:direction"),),
            batch_index=0, batch_count=2,
        )
        second = LiveSample(
            1, 0.5, 0.5, .002, False, 0x08010000,
            SourceLocation(0x08010000, "main", "main.c", 1),
            (LiveValue("speed", "f32", 0x20000020, 12.5, "00004841", node_id="typed:speed"),),
            batch_index=1, batch_count=2,
        )

        panel.append_live_sample(first)
        direction_before = panel.table.item(panel.rows["direction"], 1).text()
        panel.append_live_sample(second)

        self.assertEqual(panel.table.item(panel.rows["direction"], 1).text(), direction_before)
        self.assertIn("nhóm 2/2", panel.status.text())

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

    def test_refresh_presets_and_typed_watch_removal_keep_existing_behavior(self):
        panel = self.panel()
        self.assertEqual([panel.interval_preset_combo.itemData(i) for i in range(6)],
                         [.1, .2, .5, 1., 2., 5.])
        panel.interval_preset_combo.setCurrentIndex(4)
        self.assertEqual(panel.interval.value(), 2.)
        panel.add_compiled_watch(LiveWatch(
            "speed", "f32", 0x20000020, 4, node_id="typed:fixture:speed"
        ))
        self.assertEqual([watch.name for watch in panel.compiled_watches()], ["speed"])
        self.assertEqual(panel.watch_specs(), ())
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
            view.variable_tree_panel.set_catalog(SimpleNamespace(fingerprint="ready", roots=lambda *_args: ()))
            view._typed_source = symbols.resolve()
            view._typed_revision = (*view._revision(symbols)[:2], "ready")
            view.live_panel.start_button.setEnabled(True)
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
            view.variable_tree_panel.set_catalog(SimpleNamespace(fingerprint="ready", roots=lambda *_args: ()))
            view._typed_revision = (*view._revision(symbols)[:2], "ready")
            view.live_panel.start_button.setEnabled(True)
            with mock.patch.object(view.controller, "start") as start:
                view.live_panel.start_button.click()
                self.assertEqual(start.call_args.args[0].role, "LOCAL")
                self.assertTrue(os.path.samefile(start.call_args.args[0].symbols, symbols))
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
