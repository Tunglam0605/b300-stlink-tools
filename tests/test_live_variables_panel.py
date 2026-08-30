from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.debug_sampling import VariableSample
from b300_gui.live_variables_panel import LiveVariablesPanel


def sample(cycle: int, elapsed: float, expression: str, raw: str, numeric):
    return VariableSample(
        cycle=cycle, elapsed_seconds=elapsed, captured_at_unix_ms=1000 + cycle,
        expression=expression, raw_value=raw, numeric_value=numeric,
    )


class LiveVariablesPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_preserves_public_widget_contract_and_limits(self) -> None:
        panel = LiveVariablesPanel()
        self.assertEqual(panel.expressions.objectName(), "debugSampleExpressions")
        self.assertEqual(panel.cycles.objectName(), "debugSampleCycles")
        self.assertEqual(panel.interval.objectName(), "debugSampleInterval")
        self.assertEqual(panel.start_button.objectName(), "debugSampleStartButton")
        self.assertEqual(panel.table.objectName(), "debugSampleTable")
        self.assertEqual(panel.cycles.minimum(), 1)
        self.assertEqual(panel.cycles.maximum(), 1000)
        self.assertAlmostEqual(panel.interval.minimum(), 0.1)
        self.assertAlmostEqual(panel.interval.maximum(), 60.0)
        self.assertEqual(panel.BUFFER_CAPACITY, 2000)
        self.assertEqual(panel.PLOT_POINTS_PER_SERIES, 400)
        panel.close()

    def test_validated_request_accepts_comma_semicolon_and_newline(self) -> None:
        panel = LiveVariablesPanel()
        panel.expressions.setText("xTickCount; motorSpeed\ncurrent")
        panel.cycles.setValue(12)
        panel.interval.setValue(0.2)
        self.assertEqual(
            panel.validated_request(), ("xTickCount", "motorSpeed", "current")
        )
        panel.close()

    def test_append_batch_updates_history_table_plot_and_status(self) -> None:
        panel = LiveVariablesPanel()
        panel.cycles.setValue(5)
        panel.reset_for_sampling()
        panel.append_batch((
            sample(0, 0.0, "speed", "1.5", 1.5),
            sample(0, 0.0, "state", "RUN", None),
        ))
        panel.append_batch((
            sample(1, 0.1, "speed", "2.0", 2.0),
            sample(1, 0.1, "state", "STOP", None),
        ))
        self.assertEqual(len(panel.buffer), 4)
        self.assertEqual(panel.table.rowCount(), 2)
        self.assertEqual(panel.table.item(panel.rows["speed"], 1).text(), "2.0")
        self.assertEqual(panel.table.item(panel.rows["state"], 2).text(), "—")
        self.assertEqual(len(panel.plot.series_snapshot()), 1)
        self.assertEqual(panel.plot.series_snapshot()[0].expression, "speed")
        self.assertIn("2/5", panel.status.text())
        self.assertEqual(panel.mark_completed(panel.buffer.snapshot()), 2)
        self.assertIn("Hoàn tất 2", panel.status.text())
        panel.clear_history()
        self.assertEqual(len(panel.buffer), 0)
        self.assertEqual(panel.table.rowCount(), 0)
        self.assertEqual(panel.plot.series_snapshot(), ())
        panel.close()

    def test_control_state_is_centralized(self) -> None:
        panel = LiveVariablesPanel()
        panel.set_control_state(start_enabled=True, stop_enabled=False, history_enabled=False)
        self.assertTrue(panel.start_button.isEnabled())
        self.assertTrue(panel.expressions.isEnabled())
        self.assertFalse(panel.stop_button.isEnabled())
        self.assertFalse(panel.export_button.isEnabled())
        panel.set_control_state(start_enabled=False, stop_enabled=True, history_enabled=True)
        self.assertFalse(panel.start_button.isEnabled())
        self.assertTrue(panel.stop_button.isEnabled())
        self.assertTrue(panel.export_button.isEnabled())
        panel.close()

    def test_export_uses_panel_history_and_keeps_csv_jsonl_policy(self) -> None:
        panel = LiveVariablesPanel()
        panel.append_batch((sample(0, 0.0, "xTickCount", "123", 123.0),))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "samples.csv"
            with mock.patch(
                "b300_gui.live_variables_panel.QFileDialog.getSaveFileName",
                return_value=(str(output), "CSV (*.csv)"),
            ):
                saved = panel.export_samples()
            self.assertEqual(saved, output.resolve())
            text = output.read_text(encoding="utf-8")
        self.assertIn("xTickCount", text)
        self.assertIn("numeric_value", text)
        self.assertIn("Đã export 1", panel.status.text())
        panel.close()


if __name__ == "__main__":
    unittest.main()
