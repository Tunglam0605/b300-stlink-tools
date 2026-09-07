from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.live_monitor import LiveSample, LiveValue
from b300_core.offline_symbols import SourceLocation
from b300_gui.live_monitor_controller import LiveMonitorController
from b300_gui.production_live_panel import ProductionLivePanel
from b300_gui.views.monitor_view import MonitorView
from tests.test_typed_symbols import _build_keil_fixture


class _Cancelable:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Panel:
    def __init__(self):
        self.samples = []
        self.stale = []

    def append_live_sample(self, sample):
        self.samples.append(sample)

    def mark_stale(self, reason):
        self.stale.append(reason)


def _sample(node_id="node", *, value=7):
    return LiveSample(
        cycle=1, scheduled_elapsed_seconds=0.1, captured_elapsed_seconds=0.2,
        read_duration_seconds=0.001, overrun=False, pc=0x08010100,
        source=SourceLocation(0x08010100, "main", "main.c", 1),
        values=(LiveValue(
            "g_machine.position.x", "i16", 0x20000000, value, "0700",
            node_id=node_id,
        ),),
    )


class MonitorFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_gateway_loss_cancels_monitor_and_rejects_late_samples(self):
        panel = _Panel()
        controller = LiveMonitorController(panel)
        live = _Cancelable()
        worker = _Cancelable()
        controller._active = True
        controller._live_session = live
        controller._worker = worker

        controller.invalidate("Mất kết nối ST-Link trên Gateway.")
        controller._sample_received(_sample())

        self.assertTrue(live.cancelled)
        self.assertTrue(worker.cancelled)
        self.assertEqual(panel.samples, [])
        self.assertEqual(panel.stale, ["Mất kết nối ST-Link trên Gateway."])

    def test_gateway_loss_flushes_already_accepted_production_sample_as_stale(self):
        panel = ProductionLivePanel()
        controller = LiveMonitorController(panel)
        controller._active = True
        try:
            controller._sample_received(_sample())
            self.assertEqual(panel.table.rowCount(), 0)

            controller.invalidate("Mất kết nối Gateway.")

            self.assertEqual(panel.table.rowCount(), 1)
            self.assertEqual(len(panel.buffer), 1)
            self.assertEqual(panel.table.item(0, 1).text(), "7")
            self.assertEqual(panel.table.item(0, 4).text(), "0.200")
            self.assertIn("STALE", panel.table.item(0, 9).text())
        finally:
            panel.deleteLater()

    def test_stale_view_keeps_last_value_and_timestamp_but_marks_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            image = _build_keil_fixture(directory)
            panel = ProductionLivePanel()
            view = MonitorView(live_panel=panel)
            try:
                view.load_typed_symbols(image)
                tree = view.variable_tree_panel
                machine = next(tree.model.index(row, 0) for row in range(tree.model.rowCount())
                               if tree.model.data(tree.model.index(row, 0)) == "g_machine")
                tree.model.fetchMore(machine)
                position = next(tree.model.index(row, 0, machine)
                                for row in range(tree.model.rowCount(machine))
                                if tree.model.data(tree.model.index(row, 0, machine)) == "position")
                tree.model.fetchMore(position)
                x = next(tree.model.index(row, 0, position)
                         for row in range(tree.model.rowCount(position))
                         if tree.model.data(tree.model.index(row, 0, position)) == "x")
                node = tree.model.node_for_index(x)
                tree.tree.setCurrentIndex(x)
                tree.add_button.click()
                view._render_sample_summary(_sample(node.node_id))
                value_before = panel.table.item(0, 1).text()
                time_before = panel.table.item(0, 4).text()

                view.mark_gateway_stale("Mất liên lạc Gateway")

                self.assertEqual(panel.table.item(0, 1).text(), value_before)
                self.assertEqual(panel.table.item(0, 4).text(), time_before)
                self.assertIn("STALE", panel.table.item(0, 9).text())
                self.assertIn("STALE", tree.model.data(x.siblingAtColumn(4)))
                self.assertIn("Mất liên lạc Gateway", panel.status.text())
            finally:
                view.close()


if __name__ == "__main__":
    unittest.main()
