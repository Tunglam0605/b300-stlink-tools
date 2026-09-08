from __future__ import annotations

import unittest

from b300_core.live_analytics import LiveMonitorStore
from b300_core.live_monitor import LiveSample, LiveValue
from b300_core.offline_symbols import SourceLocation


def sample(cycle, elapsed, function, line, tick, *, overrun=False, coherent=True, read=0.01, scheduled=None):
    return LiveSample(
        cycle=cycle,
        scheduled_elapsed_seconds=elapsed if scheduled is None else scheduled,
        captured_elapsed_seconds=elapsed,
        read_duration_seconds=read,
        overrun=overrun,
        pc=0x08010000 + cycle * 2,
        source=SourceLocation(0x08010000 + cycle * 2, function, 'main.c' if function else None, line),
        values=(LiveValue('xTickCount','u32',0x20000030,tick,('%08X'%tick),coherent=coherent),),
    )


class LiveAnalyticsTests(unittest.TestCase):
    def test_delta_and_rate_use_capture_timestamps_and_reset_at_epoch_or_gap(self):
        store = LiveMonitorStore(100, max_rate_gap_seconds=1.0)
        store.append(sample(0, 0.0, 'idle', 1, 10))
        store.append(sample(1, 0.5, 'idle', 1, 16))
        stat = store.snapshot().variables[0]
        self.assertEqual(stat.delta, 6.0)
        self.assertEqual(stat.rate_per_second, 12.0)
        store.append(sample(0, 0.6, 'idle', 1, 20))
        self.assertIsNone(store.snapshot().variables[0].delta)
        store.append(sample(1, 2.0, 'idle', 1, 30))
        self.assertIsNone(store.snapshot().variables[0].rate_per_second)

    def test_threshold_crossings_capture_a_bounded_event_history(self):
        from b300_core.watch_profiles import WatchPolicy
        store = LiveMonitorStore(
            100, watch_policies=(WatchPolicy('xTickCount', maximum=10, capture=True),),
            threshold_event_capacity=2,
        )
        for cycle, value in enumerate((9, 11, 9, 11, 9, 11)):
            store.append(sample(cycle, cycle * 0.1, 'idle', 1, value))
        events = store.threshold_events()
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event.direction == 'above_maximum' for event in events))
        self.assertTrue(all(event.capture for event in events))

    def test_typed_watch_threshold_falls_back_from_node_id_to_visible_policy_path(self):
        from b300_core.watch_profiles import WatchPolicy
        policy = WatchPolicy('motor.speed', maximum=10, capture=True)
        store = LiveMonitorStore(100, watch_policies=(policy,))
        typed = LiveSample(
            cycle=0, scheduled_elapsed_seconds=0.0, captured_elapsed_seconds=0.0,
            read_duration_seconds=.01, overrun=False, pc=0x08010000,
            source=SourceLocation(0x08010000, 'idle', 'main.c', 1),
            values=(LiveValue('motor.speed', 'u32', 0x20000030, 11, '0000000B',
                              node_id='typed:machine.speed'),),
        )
        store.append(typed)
        event, = store.threshold_events()
        self.assertEqual(event.path, 'motor.speed')
        self.assertEqual(event.direction, 'above_maximum')
    def test_statistics_group_same_function_across_source_lines(self):
        store=LiveMonitorStore(100)
        store.append(sample(0,0.01,'idle',77,100))
        store.append(sample(1,0.11,'idle',87,101))
        store.append(sample(2,0.21,'can_tx',178,102,overrun=True))
        snap=store.snapshot()
        self.assertEqual(snap.timing.total_samples,3)
        self.assertEqual(snap.timing.overruns,1)
        self.assertEqual(snap.functions[0].function,'idle')
        self.assertEqual(snap.functions[0].samples,2)
        self.assertAlmostEqual(snap.functions[0].share,2/3)
        self.assertEqual(len(store.variable_series('xTickCount')),3)
        var=snap.variables[0]
        self.assertEqual((var.minimum,var.maximum,var.mean),(100.0,102.0,101.0))

    def test_transitions_compress_consecutive_same_source(self):
        store=LiveMonitorStore(100)
        store.append(sample(0,0.0,'idle',87,1))
        store.append(sample(1,0.1,'idle',87,2))
        store.append(sample(2,0.2,'queue',1407,3))
        transitions=store.transitions()
        self.assertEqual(len(transitions),2)
        self.assertEqual(transitions[0].samples,2)
        self.assertEqual(transitions[0].start_elapsed_seconds,0.0)
        self.assertEqual(transitions[0].end_elapsed_seconds,0.1)

    def test_bounded_history_keeps_whole_run_counters(self):
        store=LiveMonitorStore(100)
        for i in range(120):
            store.append(sample(i,i*0.1,'idle',87,i))
        snap=store.snapshot()
        self.assertEqual(snap.timing.total_samples,120)
        self.assertEqual(snap.timing.retained_samples,100)
        self.assertEqual(len(store.samples()),100)
        self.assertEqual(store.samples()[0].cycle,20)

    def test_incoherent_values_are_not_numeric(self):
        store=LiveMonitorStore(100)
        store.append(sample(0,0.0,'idle',87,100,coherent=False))
        snap=store.snapshot()
        self.assertEqual(snap.timing.incoherent_values,1)
        self.assertEqual(snap.variables[0].numeric_samples,0)
        self.assertIsNone(store.variable_series('xTickCount')[0].value)

    def test_clear_resets_history_and_statistics(self):
        store=LiveMonitorStore(100)
        store.append(sample(0,0.0,None,None,1))
        self.assertEqual(store.snapshot().timing.unknown_source_samples,1)
        store.clear()
        self.assertEqual(len(store),0)
        self.assertEqual(store.snapshot().timing.total_samples,0)
        self.assertEqual(store.transitions(),())


if __name__ == '__main__':
    unittest.main()
