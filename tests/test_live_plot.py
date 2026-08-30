from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.debug_sampling import VariableSample
from b300_gui.live_plot import LivePlotWidget, build_numeric_series


def sample(cycle, elapsed, expression, raw, numeric):
    return VariableSample(
        cycle=cycle, elapsed_seconds=elapsed, captured_at_unix_ms=1000 + cycle,
        expression=expression, raw_value=raw, numeric_value=numeric,
    )


class LivePlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_numeric_series_preserves_expression_order_and_trims_per_series(self) -> None:
        samples = (
            sample(0, 0.0, "speed", "1", 1.0),
            sample(0, 0.0, "state", "RUN", None),
            sample(0, 0.0, "current", "10", 10.0),
            sample(1, 0.1, "speed", "2", 2.0),
            sample(1, 0.1, "current", "20", 20.0),
            sample(2, 0.2, "speed", "3", 3.0),
        )
        series = build_numeric_series(samples, max_points_per_series=2)
        self.assertEqual(tuple(item.expression for item in series), ("speed", "current"))
        self.assertEqual(series[0].points, ((0.1, 2.0), (0.2, 3.0)))
        self.assertEqual(series[1].points, ((0.0, 10.0), (0.1, 20.0)))

    def test_plot_widget_tracks_numeric_series_and_clears(self) -> None:
        widget = LivePlotWidget(max_points_per_series=10)
        widget.resize(640, 260)
        widget.set_samples((
            sample(0, 0.0, "speed", "1.5", 1.5),
            sample(0, 0.0, "state", "RUN", None),
            sample(1, 0.1, "speed", "2.0", 2.0),
        ))
        self.assertEqual(len(widget.series_snapshot()), 1)
        self.assertEqual(widget.series_snapshot()[0].expression, "speed")
        self.assertEqual(len(widget.series_snapshot()[0].points), 2)
        widget.clear()
        self.assertEqual(widget.series_snapshot(), ())
        widget.close()

    def test_constant_and_multi_series_values_are_retained_for_painting(self) -> None:
        series = build_numeric_series((
            sample(0, 0.0, "a", "5", 5.0),
            sample(1, 0.1, "a", "5", 5.0),
            sample(0, 0.0, "b", "-2", -2.0),
            sample(1, 0.1, "b", "3", 3.0),
        ), max_points_per_series=10)
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0].points, ((0.0, 5.0), (0.1, 5.0)))
        self.assertEqual(series[1].points, ((0.0, -2.0), (0.1, 3.0)))

    def test_plot_capacity_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            build_numeric_series((), 1)
        with self.assertRaises(ValueError):
            LivePlotWidget(max_points_per_series=10001)


if __name__ == "__main__":
    unittest.main()
