from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_core.debug_sampling import (
    VariableSampleBuffer, parse_numeric_value, sample_variables,
    validate_sampling_request, write_samples,
)


class DebugSamplingTests(unittest.TestCase):
    def test_numeric_parser_accepts_only_unambiguous_scalars(self) -> None:
        self.assertEqual(parse_numeric_value("123"), 123.0)
        self.assertEqual(parse_numeric_value("-12"), -12.0)
        self.assertEqual(parse_numeric_value("0x20"), 32.0)
        self.assertEqual(parse_numeric_value("1.25e2"), 125.0)
        self.assertEqual(parse_numeric_value("true"), 1.0)
        self.assertEqual(parse_numeric_value("false"), 0.0)
        self.assertIsNone(parse_numeric_value("STATE_RUNNING"))
        self.assertIsNone(parse_numeric_value("0x08010000 <main>"))
        self.assertIsNone(parse_numeric_value("nan"))

    def test_sampling_request_is_bounded_and_unique(self) -> None:
        self.assertEqual(validate_sampling_request((" speed ", "current"), 10, 0.5), ("speed", "current"))
        for expressions, cycles, interval in (
            ((), 1, 0.5),
            (("speed", "speed"), 1, 0.5),
            (("speed",), 0, 0.5),
            (("speed",), 1001, 0.5),
            (("speed",), 1, 0.09),
            (("speed",), 1, 60.1),
        ):
            with self.assertRaises(ValueError):
                validate_sampling_request(expressions, cycles, interval)

    def test_sampler_captures_multiple_variables_per_cycle_and_does_not_burst(self) -> None:
        calls = []
        sleeps = []
        monotonic_values = iter((10.0, 10.01, 10.52, 11.03))
        wall_values = iter((100.0, 100.5, 101.0))

        def capture(expressions):
            calls.append(tuple(expressions))
            cycle = len(calls)
            return tuple(
                SimpleNamespace(expression=expression, value=str(cycle * index))
                for index, expression in enumerate(expressions, start=1)
            )

        samples = sample_variables(
            capture, ("speed", "current"), 3, 0.5,
            monotonic=lambda: next(monotonic_values),
            wall_clock=lambda: next(wall_values),
            sleeper=sleeps.append,
        )
        self.assertEqual(calls, [("speed", "current")] * 3)
        self.assertEqual(sleeps, [0.5, 0.5])
        self.assertEqual(len(samples), 6)
        self.assertEqual(samples[0].raw_value, "1")
        self.assertEqual(samples[1].raw_value, "2")
        self.assertEqual(samples[-1].raw_value, "6")
        self.assertEqual(samples[0].captured_at_unix_ms, 100000)
        self.assertAlmostEqual(samples[-1].elapsed_seconds, 1.03, places=3)

    def test_sampler_streams_each_cycle_and_can_cancel_during_interval_wait(self) -> None:
        batches = []
        waits = []
        cancelled = {"value": False}

        def wait(interval):
            waits.append(interval)
            cancelled["value"] = True
            return True

        samples = sample_variables(
            lambda expressions: tuple(
                SimpleNamespace(expression=expression, value="1") for expression in expressions
            ),
            ("speed", "current"), 5, 0.2,
            cancelled=lambda: cancelled["value"],
            waiter=wait, on_cycle=batches.append,
        )
        self.assertEqual(len(samples), 2)
        self.assertEqual(len(batches), 1)
        self.assertEqual(tuple(item.expression for item in batches[0]), ("speed", "current"))
        self.assertEqual(waits, [0.2])

    def test_sample_buffer_is_fixed_capacity_and_tracks_latest_values(self) -> None:
        batches = []
        values = iter((("1", "10"), ("2", "20"), ("3", "30")))

        def capture(expressions):
            row = next(values)
            return tuple(
                SimpleNamespace(expression=expression, value=value)
                for expression, value in zip(expressions, row)
            )

        samples = sample_variables(capture, ("speed", "current"), 3, 0.1, sleeper=lambda _value: None)
        buffer = VariableSampleBuffer(max_samples=4)
        buffer.extend(samples)
        snapshot = buffer.snapshot()
        self.assertEqual(len(snapshot), 4)
        self.assertEqual(snapshot[0].cycle, 1)
        self.assertEqual(snapshot[-1].raw_value, "30")
        latest = buffer.latest_by_expression()
        self.assertEqual(latest["speed"].raw_value, "3")
        self.assertEqual(latest["current"].raw_value, "30")
        buffer.clear()
        self.assertEqual(len(buffer), 0)
        with self.assertRaises(ValueError):
            VariableSampleBuffer(0)

    def test_sampler_rejects_capture_count_or_expression_mismatch(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "captured 0 values"):
            sample_variables(lambda _expressions: (), ("speed",), 1, 0.5)
        with self.assertRaisesRegex(RuntimeError, "expression mismatch"):
            sample_variables(
                lambda _expressions: (SimpleNamespace(expression="other", value="1"),),
                ("speed",), 1, 0.5,
            )

    def test_csv_and_jsonl_outputs_preserve_raw_and_numeric_values(self) -> None:
        samples = sample_variables(
            lambda expressions: tuple(
                SimpleNamespace(expression=expression, value=value)
                for expression, value in zip(expressions, ("12.5", "STATE_RUN"))
            ),
            ("speed", "state"), 1, 0.5,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = write_samples(root / "samples.csv", samples)
            jsonl_path = write_samples(root / "samples.jsonl", samples)
            with csv_path.open("r", encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["raw_value"], "12.5")
        self.assertEqual(float(rows[0]["numeric_value"]), 12.5)
        self.assertEqual(records[1]["raw_value"], "STATE_RUN")
        self.assertIsNone(records[1]["numeric_value"])

    def test_output_rejects_unknown_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "csv or .jsonl"):
                write_samples(Path(directory) / "samples.txt", ())


if __name__ == "__main__":
    unittest.main()
