from __future__ import annotations

import threading
import unittest

from b300_core.live_analytics import LiveMonitorStore
from b300_core.live_monitor import LiveSample, LiveValue
from b300_core.offline_symbols import SourceLocation


def make_sample(cycle: int, *, function='fnA', file='main.c', line=10, value=None,
                coherent=True, overrun=False, read_duration=0.01, lag=0.0):
    scheduled = cycle * 0.1
    captured = scheduled + lag + read_duration
    values = () if value is None and coherent else (
        LiveValue('xTickCount', 'u32', 0x20000030, value,
                  '00000000' if value is None else int(value).to_bytes(4, 'little').hex().upper(),
                  coherent=coherent),
    )
    return LiveSample(
        cycle=cycle, scheduled_elapsed_seconds=scheduled,
        captured_elapsed_seconds=captured, read_duration_seconds=read_duration,
        overrun=overrun, pc=0x08010000 + cycle * 2,
        source=SourceLocation(0x08010000 + cycle * 2, function, file, line),
        values=values,
    )


class LiveMonitorStoreTests(unittest.TestCase):
    def test_bounded_history_keeps_whole_run_statistics(self):
        store = LiveMonitorStore(100)
        for cycle in range(150):
            store.append(make_sample(cycle, value=cycle))
        self.assertEqual(len(store), 100)
        self.assertEqual(store.samples()[0].cycle, 50)
        snap = store.snapshot()
        self.assertEqual(snap.timing.total_samples, 150)
        self.assertEqual(snap.timing.retained_samples, 100)
        self.assertEqual(snap.functions[0].samples, 150)
        self.assertEqual(snap.variables[0].samples, 150)
        self.assertEqual(snap.variables[0].latest_value, 149)

    def test_function_statistics_group_multiple_lines_of_same_function(self):
        store = LiveMonitorStore(100)
        for cycle, line in enumerate((77, 87, 87, 91, 87)):
            store.append(make_sample(cycle, function='vApplicationIdleHook', line=line, value=cycle))
        snap = store.snapshot()
        self.assertEqual(len(snap.functions), 1)
        stat = snap.functions[0]
        self.assertEqual(stat.function, 'vApplicationIdleHook')
        self.assertEqual(stat.samples, 5)
        self.assertEqual(stat.line, 87)
        self.assertAlmostEqual(stat.share, 1.0)

    def test_execution_transitions_compress_consecutive_identical_sources(self):
        store = LiveMonitorStore(100)
        store.append(make_sample(0, function='A', line=1, value=1))
        store.append(make_sample(1, function='A', line=1, value=2))
        store.append(make_sample(2, function='B', line=2, value=3))
        transitions = store.transitions()
        self.assertEqual(len(transitions), 2)
        self.assertEqual(transitions[0].function, 'A')
        self.assertEqual(transitions[0].samples, 2)
        self.assertEqual(transitions[1].function, 'B')
        self.assertEqual(transitions[1].samples, 1)

    def test_variable_stats_and_series_skip_incoherent_numeric_value(self):
        store = LiveMonitorStore(100)
        store.append(make_sample(0, value=10, coherent=True))
        store.append(make_sample(1, value=None, coherent=False))
        store.append(make_sample(2, value=30, coherent=True))
        stat = store.snapshot().variables[0]
        self.assertEqual(stat.samples, 3)
        self.assertEqual(stat.coherent_samples, 2)
        self.assertEqual(stat.incoherent_samples, 1)
        self.assertEqual(stat.numeric_samples, 2)
        self.assertEqual(stat.minimum, 10.0)
        self.assertEqual(stat.maximum, 30.0)
        self.assertEqual(stat.mean, 20.0)
        series = store.variable_series('xTickCount')
        self.assertEqual([point.value for point in series], [10.0, None, 30.0])
        self.assertEqual([point.coherent for point in series], [True, False, True])

    def test_timing_statistics_report_overrun_and_schedule_lag(self):
        store = LiveMonitorStore(100)
        store.append(make_sample(0, value=1, read_duration=0.02, lag=0.01))
        store.append(make_sample(1, value=2, read_duration=0.04, lag=0.03, overrun=True))
        timing = store.snapshot().timing
        self.assertEqual(timing.overruns, 1)
        self.assertAlmostEqual(timing.mean_read_duration_seconds, 0.03)
        self.assertAlmostEqual(timing.max_read_duration_seconds, 0.04)
        self.assertAlmostEqual(timing.mean_schedule_lag_seconds, 0.02)
        self.assertAlmostEqual(timing.max_schedule_lag_seconds, 0.03)

    def test_unknown_source_and_clear_reset_statistics(self):
        store = LiveMonitorStore(100)
        store.append(make_sample(0, function=None, file=None, line=None, value=1))
        self.assertEqual(store.snapshot().timing.unknown_source_samples, 1)
        store.clear()
        snap = store.snapshot()
        self.assertEqual(snap.timing.total_samples, 0)
        self.assertEqual(store.samples(), ())
        self.assertEqual(store.transitions(), ())

    def test_thread_safe_snapshot_during_append(self):
        store = LiveMonitorStore(1000)
        failures = []
        def writer():
            try:
                for cycle in range(500):
                    store.append(make_sample(cycle, value=cycle))
            except Exception as exc:  # pragma: no cover - assertion captures thread exception
                failures.append(exc)
        thread = threading.Thread(target=writer)
        thread.start()
        while thread.is_alive():
            _ = store.snapshot()
            _ = store.samples(limit=10)
        thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(store.snapshot().timing.total_samples, 500)

    def test_rejects_variable_identity_change(self):
        store = LiveMonitorStore(100)
        store.append(make_sample(0, value=1))
        bad = LiveSample(
            1, 0.1, 0.11, 0.01, False, 0x08010002, SourceLocation(0x08010002, 'fnA', 'main.c', 10),
            (LiveValue('xTickCount', 'u16', 0x20000030, 2, '0200'),),
        )
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            store.append(bad)


if __name__ == '__main__':
    unittest.main()
